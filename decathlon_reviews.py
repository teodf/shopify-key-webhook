import json
import os
from pathlib import Path

from flask import Blueprint, Response, jsonify, request


bp = Blueprint("decathlon_reviews", __name__, url_prefix="/decathlon-reviews")


def _env(name, default=None):
    value = os.environ.get(name, default)
    if isinstance(value, str):
        value = value.strip()
    return value


def _output_path():
    return Path(_env("DECATHLON_REVIEWS_OUTPUT_FILE", "/var/data/decathlon_reviews.json"))


def _public_payload_headers(response):
    origin = _env("DECATHLON_REVIEWS_CORS_ORIGIN", "*")
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Cache-Control"] = _env("DECATHLON_REVIEWS_CACHE_CONTROL", "public, max-age=3600")
    return response


@bp.route("/reviews.json", methods=["GET", "OPTIONS"])
def public_reviews_json():
    if request.method == "OPTIONS":
        return _public_payload_headers(Response(status=204))

    output_path = _output_path()
    if not output_path.exists():
        return _public_payload_headers(jsonify({"error": "Avis Decathlon non synchronises"})), 404

    with output_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return _public_payload_headers(jsonify(payload))
