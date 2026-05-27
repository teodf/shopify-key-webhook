import datetime
import os
import time

import requests
from flask import Blueprint, Response, jsonify, request


bp = Blueprint("judgeme_reviews", __name__, url_prefix="/judgeme-reviews")

JUDGEME_REVIEWS_URL = "https://api.judge.me/api/v1/reviews"
DEFAULT_PER_PAGE = 100
DEFAULT_CACHE_SECONDS = 3600
DEFAULT_MAX_PAGES = 1000

_CACHE = {
    "payload": None,
    "expires_at": 0,
}


class JudgeMeConfigError(RuntimeError):
    pass


def _env(name, default=None):
    value = os.environ.get(name, default)
    if isinstance(value, str):
        value = value.strip()
    return value


def _cache_seconds():
    return max(0, int(_env("JUDGEME_CACHE_SECONDS", str(DEFAULT_CACHE_SECONDS))))


def _max_pages():
    return max(1, int(_env("JUDGEME_MAX_PAGES", str(DEFAULT_MAX_PAGES))))


def _public_payload_headers(response, cache_seconds=None):
    origin = _env("JUDGEME_CORS_ORIGIN", "*")
    seconds = _cache_seconds() if cache_seconds is None else cache_seconds
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Cache-Control"] = f"public, max-age={seconds}" if seconds else "no-store"
    return response


def _api_token():
    token = _env("JUDGEME_PRIVATE_API_TOKEN")
    if not token:
        raise JudgeMeConfigError("Judge.me non configure: definissez JUDGEME_PRIVATE_API_TOKEN.")
    return token


def _shop_domain():
    shop_domain = _env("JUDGEME_SHOP_DOMAIN")
    if not shop_domain:
        raise JudgeMeConfigError("Judge.me non configure: definissez JUDGEME_SHOP_DOMAIN.")
    return shop_domain


def _number(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _boolish(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "published"}


def _reviewer_name(review):
    reviewer = review.get("reviewer")
    if isinstance(reviewer, dict):
        name = reviewer.get("name")
        if name:
            return name
    return (
        review.get("reviewer_name")
        or review.get("name")
        or review.get("author")
        or "Client Footbar"
    )


def _reviewer_country(review):
    reviewer = review.get("reviewer")
    if isinstance(reviewer, dict):
        country = reviewer.get("country") or reviewer.get("country_code")
        if country:
            return country
    return review.get("country") or review.get("country_code") or ""


def _published(review):
    if "published" in review:
        return _boolish(review.get("published"))
    if "hidden" in review:
        return not _boolish(review.get("hidden"))
    if "curated" in review:
        return review.get("curated") == "ok"
    return True


def _verified_purchase(review):
    return _boolish(
        review.get("verified_buyer")
        if "verified_buyer" in review
        else review.get("verified_purchase")
        if "verified_purchase" in review
        else review.get("verified")
    )


def _normalize_review(review):
    created_at = review.get("created_at") or review.get("createdAt")
    updated_at = review.get("updated_at") or review.get("updatedAt") or created_at
    return {
        "id": str(review.get("id") or ""),
        "source": "site",
        "source_key": "judgeme",
        "rating": _number(review.get("rating")),
        "title": review.get("title") or "",
        "comment": review.get("body") or review.get("comment") or "",
        "author": _reviewer_name(review),
        "country": _reviewer_country(review),
        "create_time": created_at,
        "update_time": updated_at,
        "verified_purchase": _verified_purchase(review),
        "published": _published(review),
        "url": review.get("url") or review.get("review_url") or "",
    }


def _sort_key(review):
    return review.get("update_time") or review.get("create_time") or ""


def _average_rating(reviews):
    ratings = [review["rating"] for review in reviews if review.get("rating") is not None]
    if not ratings:
        return None
    return round(sum(ratings) / len(ratings), 2)


def _reviews_from_payload(payload):
    if isinstance(payload, dict):
        reviews = payload.get("reviews")
        return reviews if isinstance(reviews, list) else []
    if isinstance(payload, list):
        return payload
    return []


def _fetch_page(page):
    response = requests.get(
        JUDGEME_REVIEWS_URL,
        params={
            "api_token": _api_token(),
            "shop_domain": _shop_domain(),
            "per_page": DEFAULT_PER_PAGE,
            "page": page,
            "published": "true",
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:1000]
        raise RuntimeError(f"Erreur Judge.me API {response.status_code}: {detail}") from exc
    return response.json()


def fetch_judgeme_reviews():
    reviews = []
    seen_ids = set()

    for page in range(1, _max_pages() + 1):
        payload = _fetch_page(page)
        raw_reviews = _reviews_from_payload(payload)
        if not raw_reviews:
            break

        for raw_review in raw_reviews:
            normalized = _normalize_review(raw_review)
            if not normalized["published"]:
                continue
            if normalized["id"] and normalized["id"] in seen_ids:
                continue
            if normalized["id"]:
                seen_ids.add(normalized["id"])
            reviews.append(normalized)

        if len(raw_reviews) < DEFAULT_PER_PAGE:
            break

    reviews.sort(key=_sort_key, reverse=True)
    ratings = [review["rating"] for review in reviews if review.get("rating") is not None]
    return {
        "source": "judgeme",
        "manual_import": False,
        "review_count": len([review for review in reviews if (review.get("comment") or "").strip()]),
        "rating_count": len(ratings),
        "average_rating": _average_rating(reviews),
        "reviews": reviews,
        "status": "ok",
        "synced_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def _error_payload(error):
    return {
        "source": "judgeme",
        "manual_import": False,
        "review_count": 0,
        "rating_count": 0,
        "average_rating": None,
        "reviews": [],
        "status": "error",
        "synced_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "error": str(error),
    }


def _cached_payload():
    if _CACHE["payload"] and time.time() < _CACHE["expires_at"]:
        return _CACHE["payload"]
    payload = fetch_judgeme_reviews()
    _CACHE["payload"] = payload
    _CACHE["expires_at"] = time.time() + _cache_seconds()
    return payload


@bp.route("/reviews.json", methods=["GET", "OPTIONS"])
def public_reviews_json():
    if request.method == "OPTIONS":
        return _public_payload_headers(Response(status=204))

    try:
        return _public_payload_headers(jsonify(_cached_payload()))
    except Exception as exc:
        response = jsonify(_error_payload(exc))
        return _public_payload_headers(response, cache_seconds=0), 500
