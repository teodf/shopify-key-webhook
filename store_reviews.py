import datetime
import json
import os
from pathlib import Path
from urllib.parse import quote

import jwt
import pycountry
import requests
from flask import Blueprint, Response, jsonify, request
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account


ANDROID_PUBLISHER_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
GOOGLE_PLAY_REVIEWS_URL = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{package_name}/reviews"
APP_STORE_CONNECT_BASE_URL = "https://api.appstoreconnect.apple.com/v1"
APP_STORE_LOOKUP_URL = "https://itunes.apple.com/lookup"

bp = Blueprint("store_reviews", __name__, url_prefix="/store-reviews")


class StoreReviewsConfigError(RuntimeError):
    pass


def _env(name, default=None):
    value = os.environ.get(name, default)
    if isinstance(value, str):
        value = value.strip()
    return value


def _reviews_output_path():
    return Path(_env("APP_REVIEWS_OUTPUT_FILE", "/var/data/app_reviews.json"))


def _max_reviews(provider_name):
    specific = _env(f"{provider_name}_MAX_REVIEWS")
    generic = _env("APP_REVIEWS_MAX_REVIEWS", "50")
    raw_value = (specific or generic or "").lower()
    if raw_value in {"0", "all", "none", "unlimited"}:
        return None
    return max(1, int(raw_value))


def _admin_authorized():
    expected = _env("APP_REVIEWS_ADMIN_TOKEN") or _env("GOOGLE_BUSINESS_ADMIN_TOKEN")
    if not expected:
        return True
    supplied = request.headers.get("X-Admin-Token") or request.args.get("admin_token")
    return supplied == expected


def _require_admin():
    if not _admin_authorized():
        return jsonify({"error": "Non autorise"}), 401
    return None


def _public_payload_headers(response):
    origin = _env("APP_REVIEWS_CORS_ORIGIN", "*")
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Cache-Control"] = _env("APP_REVIEWS_CACHE_CONTROL", "public, max-age=3600")
    return response


def _raise_for_provider(response, provider):
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:2000]
        raise RuntimeError(f"Erreur {provider} API {response.status_code}: {detail}") from exc


def _timestamp_to_iso(timestamp):
    if not timestamp:
        return None
    seconds = int(timestamp.get("seconds", 0))
    nanos = int(timestamp.get("nanos", 0))
    value = datetime.datetime.fromtimestamp(seconds + nanos / 1_000_000_000, datetime.timezone.utc)
    return value.isoformat()


def _average_rating(reviews):
    ratings = [review.get("rating") for review in reviews if review.get("rating")]
    if not ratings:
        return None
    return round(sum(ratings) / len(ratings), 2)


def _weighted_average_from_rating_items(items):
    weighted_sum = 0
    rating_count = 0
    for item in items:
        average = item.get("average_rating")
        count = item.get("rating_count")
        if average is None or not count:
            continue
        weighted_sum += average * count
        rating_count += count
    if not rating_count:
        return None
    return {
        "average_rating": round(weighted_sum / rating_count, 6),
        "rating_count": rating_count,
    }


def _weighted_average_rating(summaries):
    weighted_sum = 0
    rating_count = 0
    for summary in summaries:
        average = summary.get("average_rating")
        count = summary.get("rating_count")
        if average is None or not count:
            continue
        weighted_sum += average * count
        rating_count += count
    if not rating_count:
        return None
    return round(weighted_sum / rating_count, 2)


def _sort_key(review):
    return review.get("update_time") or review.get("create_time") or ""


def _google_play_service_account_info():
    raw_json = _env("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON")
    if raw_json:
        return json.loads(raw_json)

    file_path = _env("GOOGLE_PLAY_SERVICE_ACCOUNT_FILE")
    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    raise StoreReviewsConfigError(
        "Google Play non configure: definissez GOOGLE_PLAY_SERVICE_ACCOUNT_JSON ou GOOGLE_PLAY_SERVICE_ACCOUNT_FILE."
    )


def _google_play_access_token():
    credentials = service_account.Credentials.from_service_account_info(
        _google_play_service_account_info(),
        scopes=[ANDROID_PUBLISHER_SCOPE],
    )
    credentials.refresh(GoogleAuthRequest())
    return credentials.token


def _normalize_google_play_review(review):
    user_comment = None
    developer_comment = None
    for comment in review.get("comments", []):
        if "userComment" in comment:
            user_comment = comment["userComment"]
        if "developerComment" in comment:
            developer_comment = comment["developerComment"]

    user_comment = user_comment or {}
    developer_comment = developer_comment or {}
    return {
        "id": review.get("reviewId"),
        "source": "google_play",
        "rating": user_comment.get("starRating"),
        "title": "",
        "comment": user_comment.get("text") or "",
        "original_comment": user_comment.get("originalText") or "",
        "author": review.get("authorName") or "",
        "language": user_comment.get("reviewerLanguage"),
        "territory": None,
        "app_version": user_comment.get("appVersionName"),
        "create_time": _timestamp_to_iso(user_comment.get("lastModified")),
        "update_time": _timestamp_to_iso(user_comment.get("lastModified")),
        "thumbs_up_count": user_comment.get("thumbsUpCount"),
        "thumbs_down_count": user_comment.get("thumbsDownCount"),
        "owner_reply": {
            "comment": developer_comment.get("text") or "",
            "update_time": _timestamp_to_iso(developer_comment.get("lastModified")),
        } if developer_comment else None,
    }


def fetch_google_play_reviews():
    package_name = _env("GOOGLE_PLAY_PACKAGE_NAME")
    if not package_name:
        raise StoreReviewsConfigError("Google Play non configure: definissez GOOGLE_PLAY_PACKAGE_NAME.")

    max_reviews = _max_reviews("GOOGLE_PLAY")
    access_token = _google_play_access_token()
    reviews = []
    page_token = None
    url = GOOGLE_PLAY_REVIEWS_URL.format(package_name=quote(package_name, safe=""))

    while max_reviews is None or len(reviews) < max_reviews:
        page_size = 100 if max_reviews is None else min(100, max_reviews - len(reviews))
        params = {"maxResults": page_size}
        translation_language = _env("GOOGLE_PLAY_TRANSLATION_LANGUAGE")
        if translation_language:
            params["translationLanguage"] = translation_language
        if page_token:
            params["token"] = page_token

        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params=params,
            timeout=30,
        )
        _raise_for_provider(response, "Google Play")
        payload = response.json()
        reviews.extend(_normalize_google_play_review(review) for review in payload.get("reviews", []))
        page_token = (payload.get("tokenPagination") or {}).get("nextPageToken")
        if not page_token:
            break

    if max_reviews is not None:
        reviews = reviews[:max_reviews]
    return {
        "status": "ok",
        "package_name": package_name,
        "review_count": len(reviews),
        "average_rating": _average_rating(reviews),
        "reviews": reviews,
    }


def _app_store_private_key():
    key = _env("APP_STORE_CONNECT_PRIVATE_KEY")
    if key:
        return key.replace("\\n", "\n")

    file_path = _env("APP_STORE_CONNECT_PRIVATE_KEY_FILE")
    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    raise StoreReviewsConfigError(
        "App Store non configure: definissez APP_STORE_CONNECT_PRIVATE_KEY ou APP_STORE_CONNECT_PRIVATE_KEY_FILE."
    )


def _app_store_token():
    issuer_id = _env("APP_STORE_CONNECT_ISSUER_ID")
    key_id = _env("APP_STORE_CONNECT_KEY_ID")
    if not issuer_id or not key_id:
        raise StoreReviewsConfigError(
            "App Store non configure: definissez APP_STORE_CONNECT_ISSUER_ID et APP_STORE_CONNECT_KEY_ID."
        )

    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    return jwt.encode(
        {"iss": issuer_id, "iat": now, "exp": now + 20 * 60, "aud": "appstoreconnect-v1"},
        _app_store_private_key(),
        algorithm="ES256",
        headers={"kid": key_id, "typ": "JWT"},
    )


def _app_store_get(path_or_url, params=None):
    url = path_or_url if path_or_url.startswith("https://") else f"{APP_STORE_CONNECT_BASE_URL}{path_or_url}"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {_app_store_token()}", "Accept": "application/json"},
        params=params,
        timeout=30,
    )
    _raise_for_provider(response, "App Store Connect")
    return response.json()


def _normalize_app_store_review(review, responses_by_id):
    attributes = review.get("attributes") or {}
    relationship_data = (((review.get("relationships") or {}).get("response") or {}).get("data") or {})
    response_payload = responses_by_id.get(relationship_data.get("id"), {})
    response_attributes = response_payload.get("attributes") or {}
    owner_reply = None
    if response_attributes:
        owner_reply = {
            "comment": response_attributes.get("responseBody") or "",
            "update_time": response_attributes.get("lastModifiedDate"),
            "state": response_attributes.get("state"),
        }

    return {
        "id": review.get("id"),
        "source": "app_store",
        "rating": attributes.get("rating"),
        "title": attributes.get("title") or "",
        "comment": attributes.get("body") or "",
        "original_comment": "",
        "author": attributes.get("reviewerNickname") or "",
        "language": None,
        "territory": attributes.get("territory"),
        "app_version": None,
        "create_time": attributes.get("createdDate"),
        "update_time": attributes.get("createdDate"),
        "thumbs_up_count": None,
        "thumbs_down_count": None,
        "owner_reply": owner_reply,
    }


def list_app_store_apps():
    params = {"limit": 200, "fields[apps]": "name,bundleId,sku,primaryLocale"}
    bundle_id = _env("APP_STORE_BUNDLE_ID")
    if bundle_id:
        params["filter[bundleId]"] = bundle_id
    return _app_store_get("/apps", params=params)


def fetch_app_store_public_rating(app_id):
    country = _env("APP_STORE_LOOKUP_COUNTRY")
    if country:
        return _fetch_app_store_public_rating_for_country(app_id, country.lower())

    countries = fetch_app_store_available_lookup_countries(app_id)
    country_ratings = [
        _fetch_app_store_public_rating_for_country(app_id, country)
        for country in countries
    ]
    country_ratings = [rating for rating in country_ratings if rating]
    aggregate = _weighted_average_from_rating_items(country_ratings)
    if not aggregate:
        return None
    return {
        **aggregate,
        "mode": "available_territories_aggregate",
        "country_count": len(country_ratings),
        "countries": country_ratings,
    }


def _territory_code_to_lookup_country(territory_code):
    code = (territory_code or "").strip()
    if not code:
        return None
    if len(code) == 2:
        return code.lower()

    aliases = {
        "ANT": "an",
        "XKX": "xk",
    }
    if code.upper() in aliases:
        return aliases[code.upper()]

    country = pycountry.countries.get(alpha_3=code.upper())
    return country.alpha_2.lower() if country else None


def fetch_app_store_available_lookup_countries(app_id):
    countries = []
    seen = set()
    next_url = None
    params = {
        "include": "territoryAvailabilities",
        "limit[territoryAvailabilities]": 200,
    }

    while True:
        payload = _app_store_get(
            next_url or f"/apps/{app_id}/appAvailabilityV2",
            params=params if not next_url else None,
        )
        for item in payload.get("included", []):
            if "territory" not in (item.get("type") or "").lower():
                continue

            attributes = item.get("attributes") or {}
            raw_code = (
                attributes.get("territory")
                or attributes.get("territoryCode")
                or attributes.get("countryCode")
                or item.get("id")
            )
            country = _territory_code_to_lookup_country(raw_code)
            if country and country not in seen:
                seen.add(country)
                countries.append(country)

        next_url = (payload.get("links") or {}).get("next")
        if not next_url:
            break

    return countries


def _fetch_app_store_public_rating_for_country(app_id, country):
    params = {"id": app_id}
    if country:
        params["country"] = country.lower()

    response = requests.get(APP_STORE_LOOKUP_URL, params=params, timeout=30)
    _raise_for_provider(response, "App Store Lookup")
    results = response.json().get("results", [])
    if not results:
        return None

    app = results[0]
    return {
        "average_rating": app.get("averageUserRating"),
        "rating_count": app.get("userRatingCount"),
        "average_rating_current_version": app.get("averageUserRatingForCurrentVersion"),
        "rating_count_current_version": app.get("userRatingCountForCurrentVersion"),
        "country": country,
        "track_view_url": app.get("trackViewUrl"),
    }


def fetch_app_store_reviews():
    app_id = _env("APP_STORE_APP_ID")
    if not app_id:
        raise StoreReviewsConfigError("App Store non configure: definissez APP_STORE_APP_ID.")

    max_reviews = _max_reviews("APP_STORE")
    reviews = []
    next_url = None
    params = {
        "limit": 200 if max_reviews is None else min(200, max_reviews),
        "sort": "-createdDate",
        "include": "response",
        "fields[customerReviews]": "rating,title,body,reviewerNickname,createdDate,territory,response",
        "fields[customerReviewResponses]": "responseBody,lastModifiedDate,state,review",
    }
    territory = _env("APP_STORE_TERRITORY")
    if territory:
        params["filter[territory]"] = territory

    while max_reviews is None or len(reviews) < max_reviews:
        payload = _app_store_get(next_url or f"/apps/{app_id}/customerReviews", params=params if not next_url else None)
        responses_by_id = {
            item.get("id"): item
            for item in payload.get("included", [])
            if item.get("type") == "customerReviewResponses"
        }
        reviews.extend(_normalize_app_store_review(review, responses_by_id) for review in payload.get("data", []))
        next_url = (payload.get("links") or {}).get("next")
        if not next_url:
            break

    if max_reviews is not None:
        reviews = reviews[:max_reviews]
    public_rating = fetch_app_store_public_rating(app_id)
    review_average_rating = _average_rating(reviews)
    return {
        "status": "ok",
        "app_id": app_id,
        "territory": territory,
        "review_count": len(reviews),
        "average_rating": public_rating.get("average_rating") if public_rating else review_average_rating,
        "rating_count": public_rating.get("rating_count") if public_rating else None,
        "public_rating": public_rating,
        "review_average_rating": review_average_rating,
        "reviews": reviews,
    }


def _safe_fetch(provider_name, fetcher):
    try:
        return fetcher()
    except StoreReviewsConfigError as exc:
        return {"status": "skipped", "reason": str(exc), "reviews": []}
    except Exception as exc:
        return {"status": "error", "reason": str(exc), "reviews": []}


def _save_reviews_payload(payload):
    output_path = _reviews_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)


def sync_store_reviews():
    sources = {
        "google_play": _safe_fetch("google_play", fetch_google_play_reviews),
        "app_store": _safe_fetch("app_store", fetch_app_store_reviews),
    }
    combined_reviews = []
    for source in sources.values():
        combined_reviews.extend(source.get("reviews", []))
    combined_reviews.sort(key=_sort_key, reverse=True)

    payload = {
        "source": "app_stores",
        "synced_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "review_count": len(combined_reviews),
        "average_rating": _weighted_average_rating(sources.values()) or _average_rating(combined_reviews),
        "review_average_rating": _average_rating(combined_reviews),
        "sources": sources,
        "reviews": combined_reviews,
    }
    _save_reviews_payload(payload)
    return payload


@bp.route("/sync", methods=["GET", "POST"])
def sync_reviews_route():
    admin_error = _require_admin()
    if admin_error:
        return admin_error
    return jsonify(sync_store_reviews())


@bp.route("/reviews.json", methods=["GET", "OPTIONS"])
def public_reviews_json():
    if request.method == "OPTIONS":
        return _public_payload_headers(Response(status=204))

    output_path = _reviews_output_path()
    if not output_path.exists():
        return _public_payload_headers(jsonify({"error": "Avis stores non synchronises"})), 404

    with output_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return _public_payload_headers(jsonify(payload))


@bp.route("/app-store/apps", methods=["GET"])
def app_store_apps_route():
    admin_error = _require_admin()
    if admin_error:
        return admin_error
    return jsonify(list_app_store_apps())
