import datetime
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlencode

import requests
from flask import Blueprint, Response, jsonify, redirect, request


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
GOOGLE_LOCATIONS_BASE_URL = "https://mybusinessbusinessinformation.googleapis.com/v1"
GOOGLE_REVIEWS_BASE_URL = "https://mybusiness.googleapis.com/v4"
BUSINESS_MANAGE_SCOPE = "https://www.googleapis.com/auth/business.manage"

bp = Blueprint("google_business_reviews", __name__, url_prefix="/google-business")


class GoogleBusinessConfigError(RuntimeError):
    pass


@bp.errorhandler(GoogleBusinessConfigError)
def handle_config_error(error):
    return jsonify({"error": str(error)}), 500


@bp.errorhandler(RuntimeError)
def handle_runtime_error(error):
    return jsonify({"error": str(error)}), 502


def _env(name, default=None):
    value = os.environ.get(name, default)
    if isinstance(value, str):
        value = value.strip()
    return value


def _required_env(name):
    value = _env(name)
    if not value:
        raise GoogleBusinessConfigError(f"Variable d'environnement manquante: {name}")
    return value


def _token_file_path():
    return Path(_env("GOOGLE_BUSINESS_TOKEN_FILE", "google_business_token.json"))


def _reviews_output_path():
    return Path(_env("GOOGLE_BUSINESS_REVIEWS_OUTPUT_FILE", "google_business_reviews.json"))


def _admin_authorized():
    expected = _env("GOOGLE_BUSINESS_ADMIN_TOKEN")
    if not expected:
        return True
    supplied = request.headers.get("X-Admin-Token") or request.args.get("admin_token")
    return supplied == expected


def _require_admin():
    if not _admin_authorized():
        return jsonify({"error": "Non autorise"}), 401
    return None


def _client_credentials():
    return {
        "client_id": _required_env("GOOGLE_BUSINESS_CLIENT_ID"),
        "client_secret": _required_env("GOOGLE_BUSINESS_CLIENT_SECRET"),
        "redirect_uri": _required_env("GOOGLE_BUSINESS_REDIRECT_URI"),
    }


def _load_token_payload():
    refresh_token = _env("GOOGLE_BUSINESS_REFRESH_TOKEN")
    if refresh_token:
        return {"refresh_token": refresh_token}

    token_file = _token_file_path()
    if not token_file.exists():
        raise GoogleBusinessConfigError(
            "Aucun refresh token Google Business Profile. Lancez /google-business/oauth/start "
            "ou configurez GOOGLE_BUSINESS_REFRESH_TOKEN."
        )

    with token_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_token_payload(payload):
    token_file = _token_file_path()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    with token_file.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)


def _get_access_token():
    creds = _client_credentials()
    token_payload = _load_token_payload()
    refresh_token = token_payload.get("refresh_token")
    if not refresh_token:
        raise GoogleBusinessConfigError("Le refresh token Google Business Profile est absent.")

    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    _raise_for_google(response)
    return response.json()["access_token"]


def _google_headers(access_token):
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


def _raise_for_google(response):
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:2000]
        raise RuntimeError(f"Erreur Google API {response.status_code}: {detail}") from exc


def _google_get(url, access_token, params=None):
    response = requests.get(url, headers=_google_headers(access_token), params=params, timeout=30)
    _raise_for_google(response)
    return response.json()


def _account_id(account_name_or_id):
    return (account_name_or_id or "").replace("accounts/", "").strip()


def _location_id(location_name_or_id):
    value = (location_name_or_id or "").strip()
    if "/locations/" in value:
        return value.rsplit("/locations/", 1)[1]
    if value.startswith("locations/"):
        return value.split("/", 1)[1]
    return value


def _star_rating_to_number(value):
    mapping = {
        "ONE": 1,
        "TWO": 2,
        "THREE": 3,
        "FOUR": 4,
        "FIVE": 5,
    }
    if isinstance(value, int):
        return value
    return mapping.get((value or "").upper())


def _normalize_review(review):
    reviewer = review.get("reviewer") or {}
    reply = review.get("reviewReply") or {}
    return {
        "id": review.get("reviewId") or review.get("name", "").rsplit("/", 1)[-1],
        "name": review.get("name"),
        "rating": _star_rating_to_number(review.get("starRating")),
        "star_rating": review.get("starRating"),
        "comment": review.get("comment") or "",
        "author": reviewer.get("displayName") or "",
        "profile_photo_url": reviewer.get("profilePhotoUrl") or "",
        "create_time": review.get("createTime"),
        "update_time": review.get("updateTime"),
        "owner_reply": {
            "comment": reply.get("comment") or "",
            "update_time": reply.get("updateTime"),
        } if reply else None,
    }


def _public_payload_headers(response):
    origin = _env("GOOGLE_BUSINESS_REVIEWS_CORS_ORIGIN", "*")
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Cache-Control"] = _env("GOOGLE_BUSINESS_REVIEWS_CACHE_CONTROL", "public, max-age=3600")
    return response


def _fetch_all_accounts(access_token):
    accounts = []
    page_token = None
    while True:
        params = {"pageSize": 20}
        if page_token:
            params["pageToken"] = page_token
        payload = _google_get(GOOGLE_ACCOUNTS_URL, access_token, params=params)
        accounts.extend(payload.get("accounts", []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            return accounts


def _fetch_locations_for_account(access_token, account_name):
    locations = []
    page_token = None
    read_mask = _env(
        "GOOGLE_BUSINESS_LOCATIONS_READ_MASK",
        "name,title,storefrontAddress,metadata,phoneNumbers,websiteUri",
    )
    while True:
        params = {"readMask": read_mask, "pageSize": 100}
        if page_token:
            params["pageToken"] = page_token
        url = f"{GOOGLE_LOCATIONS_BASE_URL}/{account_name}/locations"
        payload = _google_get(url, access_token, params=params)
        locations.extend(payload.get("locations", []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            return locations


def _fetch_reviews(access_token, account_id, location_id):
    reviews = []
    page_token = None
    max_reviews = max(1, int(_env("GOOGLE_BUSINESS_MAX_REVIEWS", "50")))
    order_by = _env("GOOGLE_BUSINESS_REVIEWS_ORDER_BY", "updateTime desc")
    summary = {}

    while True:
        params = {"pageSize": min(50, max_reviews)}
        if order_by:
            params["orderBy"] = order_by
        if page_token:
            params["pageToken"] = page_token

        parent = f"accounts/{_account_id(account_id)}/locations/{_location_id(location_id)}"
        payload = _google_get(f"{GOOGLE_REVIEWS_BASE_URL}/{parent}/reviews", access_token, params=params)
        summary = {
            "average_rating": payload.get("averageRating"),
            "total_review_count": payload.get("totalReviewCount"),
        }
        reviews.extend(_normalize_review(review) for review in payload.get("reviews", []))

        page_token = payload.get("nextPageToken")
        if not page_token or len(reviews) >= max_reviews:
            return {
                **summary,
                "reviews": reviews[:max_reviews],
            }


def sync_google_business_reviews(account_id=None, location_id=None):
    access_token = _get_access_token()
    account_id = account_id or _required_env("GOOGLE_BUSINESS_ACCOUNT_ID")
    location_id = location_id or _required_env("GOOGLE_BUSINESS_LOCATION_ID")
    fetched = _fetch_reviews(access_token, account_id, location_id)
    payload = {
        "source": "google_business_profile",
        "account_id": _account_id(account_id),
        "location_id": _location_id(location_id),
        "synced_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        **fetched,
    }
    _save_reviews_payload(payload)
    return payload


def _save_reviews_payload(payload):
    output_path = _reviews_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)


@bp.route("/oauth/start", methods=["GET"])
def oauth_start():
    admin_error = _require_admin()
    if admin_error:
        return admin_error

    creds = _client_credentials()
    state = _env("GOOGLE_BUSINESS_OAUTH_STATE") or secrets.token_urlsafe(24)
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": creds["redirect_uri"],
        "response_type": "code",
        "scope": BUSINESS_MANAGE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return redirect(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@bp.route("/oauth/callback", methods=["GET"])
def oauth_callback():
    expected_state = _env("GOOGLE_BUSINESS_OAUTH_STATE")
    if expected_state and request.args.get("state") != expected_state:
        return jsonify({"error": "OAuth state invalide"}), 400

    error = request.args.get("error")
    if error:
        return jsonify({"error": error}), 400

    code = request.args.get("code")
    if not code:
        return jsonify({"error": "Code OAuth manquant"}), 400

    creds = _client_credentials()
    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "redirect_uri": creds["redirect_uri"],
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    _raise_for_google(response)
    payload = response.json()
    _save_token_payload(payload)
    refresh_token_response = {}
    if _env("GOOGLE_BUSINESS_EXPOSE_REFRESH_TOKEN", "").lower() == "true":
        refresh_token_response["refresh_token"] = payload.get("refresh_token")
    return jsonify(
        {
            "message": "Refresh token Google Business Profile enregistre.",
            "token_file": str(_token_file_path()),
            "has_refresh_token": bool(payload.get("refresh_token")),
            **refresh_token_response,
            "next_step": "Appelez /google-business/accounts pour trouver GOOGLE_BUSINESS_ACCOUNT_ID et GOOGLE_BUSINESS_LOCATION_ID.",
        }
    )


@bp.route("/accounts", methods=["GET"])
def list_accounts_and_locations():
    admin_error = _require_admin()
    if admin_error:
        return admin_error

    access_token = _get_access_token()
    accounts = _fetch_all_accounts(access_token)
    result = []
    for account in accounts:
        locations = _fetch_locations_for_account(access_token, account["name"])
        result.append(
            {
                "account": account,
                "account_id": _account_id(account.get("name")),
                "locations": [
                    {
                        **location,
                        "location_id": _location_id(location.get("name")),
                    }
                    for location in locations
                ],
            }
        )
    return jsonify({"accounts": result})


@bp.route("/reviews/sync", methods=["GET", "POST"])
def sync_reviews_route():
    admin_error = _require_admin()
    if admin_error:
        return admin_error

    data = request.get_json(silent=True) or {}
    account_id = request.args.get("account_id") or data.get("account_id")
    location_id = request.args.get("location_id") or data.get("location_id")
    payload = sync_google_business_reviews(account_id=account_id, location_id=location_id)
    return jsonify(payload)


@bp.route("/reviews.json", methods=["GET", "OPTIONS"])
def public_reviews_json():
    if request.method == "OPTIONS":
        return _public_payload_headers(Response(status=204))

    output_path = _reviews_output_path()
    if not output_path.exists():
        return _public_payload_headers(jsonify({"error": "Avis non synchronises"})), 404

    with output_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return _public_payload_headers(jsonify(payload))
