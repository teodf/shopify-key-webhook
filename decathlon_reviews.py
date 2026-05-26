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


def _admin_authorized():
    expected = _env("DECATHLON_REVIEWS_ADMIN_TOKEN") or _env("APP_REVIEWS_ADMIN_TOKEN") or _env("GOOGLE_BUSINESS_ADMIN_TOKEN")
    if not expected:
        return True
    supplied = request.headers.get("X-Admin-Token") or request.args.get("admin_token")
    return supplied == expected


def _require_admin():
    if not _admin_authorized():
        return jsonify({"error": "Non autorise"}), 401
    return None


def _public_payload_headers(response):
    origin = _env("DECATHLON_REVIEWS_CORS_ORIGIN", "*")
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Cache-Control"] = _env("DECATHLON_REVIEWS_CACHE_CONTROL", "public, max-age=3600")
    return response


def _validate_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Le JSON doit etre un objet.")
    if payload.get("source") != "decathlon":
        raise ValueError("Le champ source doit etre 'decathlon'.")
    if not isinstance(payload.get("reviews"), list):
        raise ValueError("Le champ reviews doit etre une liste.")


def _save_payload(payload):
    output_path = _output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
    tmp_path.replace(output_path)


@bp.route("/upload", methods=["POST"])
def upload_reviews_json():
    admin_error = _require_admin()
    if admin_error:
        return admin_error

    uploaded_file = request.files.get("file")
    if uploaded_file:
        raw_payload = uploaded_file.read().decode("utf-8")
    else:
        raw_payload = request.get_data(as_text=True)

    if not raw_payload.strip():
        return jsonify({"error": "Fichier JSON manquant."}), 400

    try:
        payload = json.loads(raw_payload)
        _validate_payload(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return jsonify({"error": f"JSON Decathlon invalide: {exc}"}), 400

    _save_payload(payload)
    return jsonify({
        "status": "ok",
        "output": str(_output_path()),
        "review_count": len(payload["reviews"]),
        "average_rating": payload.get("average_rating"),
    })


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
