from flask import Flask, request, jsonify
import logging
import datetime
import os
import json
import os.path
import pickle
import re
import requests
from urllib.parse import quote
import subprocess
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Permissions demandées : lecture + écriture sur Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'credentials.json'

# Config
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
TEMPLATE_ID_FR = "d-da4295a9f558493a8b6988af60e501de"  # Français
TEMPLATE_ID_EN = "d-0314abc9f83a4ab3bc9c3068b9b0e2a1"  # Anglais
FROM_EMAIL = "help@footbar.com"  # adresse expéditrice

# Mirakl Décathlon
MIRAKL_API_BASE_URL = os.environ.get("MIRAKL_API_BASE_URL", "https://marketplace-decathlon-eu.mirakl.net")
MIRAKL_API_KEY = os.environ.get("MIRAKL_API_KEY")
MIRAKL_STATE_FILE = os.environ.get("MIRAKL_STATE_FILE", "mirakl_state.json")

# Amazon SP-API
AMAZON_LWA_CLIENT_ID = os.environ.get("AMAZON_LWA_CLIENT_ID")
AMAZON_LWA_CLIENT_SECRET = os.environ.get("AMAZON_LWA_CLIENT_SECRET")
AMAZON_LWA_REFRESH_TOKEN = os.environ.get("AMAZON_LWA_REFRESH_TOKEN")
AMAZON_SP_API_ACCESS_KEY = os.environ.get("AMAZON_SP_API_ACCESS_KEY")
AMAZON_SP_API_SECRET_KEY = os.environ.get("AMAZON_SP_API_SECRET_KEY")
AMAZON_SP_API_ENDPOINT = os.environ.get("AMAZON_SP_API_ENDPOINT", "https://sellingpartnerapi-eu.amazon.com")
AMAZON_SP_API_REGION = os.environ.get("AMAZON_SP_API_REGION", "eu-west-1")
AMAZON_STATE_FILE = os.environ.get("AMAZON_STATE_FILE", "amazon_state.json")
# Marketplace IDs (peuvent être combinés avec des virgules)
AMAZON_MARKETPLACE_IDS = os.environ.get(
    "AMAZON_MARKETPLACE_IDS",
    "A13V1IB3VIYZZH,A1PA6795UKMFR9,APJ6JRA9NG5V4,A1RKKUPIHCS9HS,A1805IZSGTT6HS,AMEN7PMS3EDWL"
)

# Logs Render (flush direct)
logging.basicConfig(level=logging.INFO, force=True)

def log(msg):
    print(f"[LOG] {msg}", flush=True)

app = Flask(__name__)

# Configuration par produit (routing via SKU)
PRODUCT_CONFIG = {
    "FOOTBAR_GOLD_1_AN": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Feuille 1!A1:E",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "FOOTBAR_GOLD_1_AN_BUNDLE": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Feuille 1!A1:E",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "B2C001_BUNDLE": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Bundle!A1:E",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "B2C001_BUNDLE_LIFE": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Bundle à vie!A1:E",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "B2C001_BUNDLE_LIFE_10": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Bundle à vie!A1:E",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "B2C001_BUNDLE_LIFE_12": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Bundle à vie!A1:E",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "B2C001_BUNDLE_LIFE_14": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Bundle à vie!A1:E",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "B2C001_BUNDLE_LIFE_S": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Bundle à vie!A1:E",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "B2C001_BUNDLE_LIFE_M": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Bundle à vie!A1:E",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "B2C001_BUNDLE_LIFE_L": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Bundle à vie!A1:E",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "B2C001_BUNDLE_LIFE_XL": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Bundle à vie!A1:E",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "S302294": { # produit ajouté suite mauvaise config Quentin
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Bundle à vie!A1:E",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "FOOTBAR_TEAM_1_MOIS": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Plateforme Coach 1 mois!A1:E",
        "template_fr": "d-b3293a2f976b4821aa8bd9ad756cf372",
        "template_en": "d-dcae1d5d0d9e419cbf485f65a483146c",
    },
    "FOOTBAR_TEAM_1_AN": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Plateforme Coach 1 an!A1:E",
        "template_fr": "d-b3293a2f976b4821aa8bd9ad756cf372",
        "template_en": "d-dcae1d5d0d9e419cbf485f65a483146c",
    },
    "FOOTBAR_TEAM_2_ANS": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Plateforme Coach 2 ans!A1:E",
        "template_fr": "d-b3293a2f976b4821aa8bd9ad756cf372",
        "template_en": "d-dcae1d5d0d9e419cbf485f65a483146c",
    },
}

# Config par motifs (regex). Permet de grouper plusieurs SKU sous une même config
PRODUCT_REGEX_CONFIG = [
    (re.compile(r"^B2B(015|020|030)_1_MOIS$"), {
    "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
    "range_name": "Plateforme Coach 1 mois!A1:E",
    "template_fr": "d-8727718ed5ea4273abd1ed1324d2e4f6",
    "template_en": "d-97d04b88657d42859465132374a0fa2e",
    }),
    (re.compile(r"^B2B(015|020|030)_1_AN$"), {
    "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
    "range_name": "Plateforme Coach 1 an!A1:E",
    "template_fr": "d-8727718ed5ea4273abd1ed1324d2e4f6",
    "template_en": "d-97d04b88657d42859465132374a0fa2e",
    }),
    (re.compile(r"^B2B(015|020|030)_2_ANS$"), {
    "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
    "range_name": "Plateforme Coach 2 ans!A1:E",
    "template_fr": "d-8727718ed5ea4273abd1ed1324d2e4f6",
    "template_en": "d-97d04b88657d42859465132374a0fa2e",
    })
]

def find_product_config_for_sku(sku):
    # 1) Correspondance exacte
    if sku in PRODUCT_CONFIG:
        return PRODUCT_CONFIG[sku]
    # 2) Correspondance par regex
    for pattern, cfg in PRODUCT_REGEX_CONFIG:
        if pattern.match(sku):
            return cfg
    return None

def get_sheets_service():
    # Détecte automatiquement le type de credentials et s'adapte:
    # - Production/Render: privilégie un compte de service (env GOOGLE_CREDENTIALS ou credentials.json type service_account)
    # - Local: OAuth installed app (credentials.json type installed) avec cache token.pickle

    # Charge les credentials depuis env ou fichier
    creds_json_str = os.environ.get('GOOGLE_CREDENTIALS')
    creds_file_path = os.environ.get('CREDENTIALS_FILE')
    creds_info = None
    if creds_json_str:
        try:
            creds_info = json.loads(creds_json_str)
        except Exception:
            raise RuntimeError("GOOGLE_CREDENTIALS n'est pas un JSON valide.")
    elif creds_file_path and os.path.exists(creds_file_path):
        with open(creds_file_path, 'r') as f:
            creds_info = json.load(f)
    else:
        if not os.path.exists('credentials.json'):
            raise RuntimeError("Aucun credentials trouvé. Définissez GOOGLE_CREDENTIALS, CREDENTIALS_FILE ou ajoutez credentials.json.")
        with open('credentials.json', 'r') as f:
            creds_info = json.load(f)

    # Chemin compte de service
    if isinstance(creds_info, dict) and creds_info.get('type') == 'service_account':
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        return build('sheets', 'v4', credentials=creds)

    # Chemin OAuth client (installed/web) - pour local uniquement
    is_render = os.environ.get('RENDER', '') == 'true' or os.environ.get('RENDER_SERVICE_ID')
    is_production = os.environ.get('ENV') == 'production' or os.environ.get('PYTHON_ENV') == 'production'
    if is_render or is_production:
        raise RuntimeError("Le credentials fourni n'est pas un compte de service. Utilisez un JSON type 'service_account' en production (Render).")

    # Local dev: OAuth installed/web
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            try:
                creds = pickle.load(token)
            except Exception:
                creds = None
    if not creds or not getattr(creds, 'valid', False):
        if creds and getattr(creds, 'expired', False) and getattr(creds, 'refresh_token', None):
            creds.refresh(Request())
            with open('token.pickle', 'wb') as token_out:
                pickle.dump(creds, token_out)
        else:
            # Supporte formats 'installed' ou 'web'
            flow = InstalledAppFlow.from_client_config(creds_info, SCOPES)
            creds = flow.run_local_server(port=0)
            with open('token.pickle', 'wb') as token_out:
                pickle.dump(creds, token_out)
    return build('sheets', 'v4', credentials=creds)

def read_keys(spreadsheet_id, range_name):
    service = get_sheets_service()
    sheet = service.spreadsheets()
    result = sheet.values().get(spreadsheetId=spreadsheet_id,
                                range=range_name).execute()
    values = result.get('values', [])
    return values

def write_keys(spreadsheet_id, range_name, values):
    service = get_sheets_service()
    body = {'values': values}
    result = service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=range_name,
        valueInputOption='RAW', body=body).execute()
    return result

# 📩 Fonction d'envoi d'email simple pour Amazon
def send_amazon_simple_email(to_email, licence_key, order_id, language_code="fr"):
    """Envoie un email simple pour les commandes Amazon sans template"""
    try:
        log(f"📤 Envoi email Amazon simple à {to_email} avec clé {licence_key} pour commande {order_id} (langue: {language_code})")

        # Déterminer si on utilise le français ou l'anglais
        is_french = language_code and language_code.lower().startswith("fr")
        
        if is_french:
            email_content = f"""Bonjour,

Concernant votre commande Amazon {order_id},  
voici le code requis pour accéder au service inclus avec votre produit Footbar :

Code : {licence_key}

Si vous rencontrez une difficulté technique pour l'utiliser, merci de répondre à ce message.

Cordialement,  
Footbar"""
            subject = "Votre code d'accès Footbar pour la commande {order_id}"
        else:
            email_content = f"""Hello,

Regarding your Amazon order {order_id},  
here is the code required to access the service included with your Footbar product:

Code: {licence_key}

If you encounter any technical difficulties using it, please reply to this message.

Best regards,  
Footbar"""
            subject = "Your Footbar access code for order {order_id}"

        message = Mail(
            from_email=(FROM_EMAIL, "Footbar"),
            to_emails=to_email,
            subject=subject,
            plain_text_content=email_content
        )

        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        log(f"📨 Réponse SendGrid: {response.status_code}")
        log(f"📨 Headers: {response.headers}")
        return response.status_code == 202

    except Exception as e:
        log(f"❌ Erreur SendGrid : {e}")
        return False

# 📩 Fonction d'envoi d'email
def send_email_with_template(to_email, licence_key, language_code, template_fr_override=None, template_en_override=None, order_id=None):
    try:
        log(f"📤 Envoi email à {to_email} avec clé {licence_key} en langue {language_code}")

        # Choix du template en fonction de la langue
        if language_code and language_code.lower().startswith("fr"):
            template_id = template_fr_override if template_fr_override and template_fr_override != "A DEFINIR" else TEMPLATE_ID_FR
        else:
            template_id = template_en_override if template_en_override and template_en_override != "A DEFINIR" else TEMPLATE_ID_EN

        message = Mail(
            from_email=(FROM_EMAIL, "Footbar"),
            to_emails=to_email
        )
        # Construire les données du template
        template_data = {
            "licence_key": licence_key
        }
        # Ajouter le numéro de commande si fourni (pour Amazon)
        if order_id:
            template_data["order_id"] = order_id
        message.dynamic_template_data = template_data
        message.template_id = template_id

        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        log(f"📨 Réponse SendGrid: {response.status_code}")
        log(f"📨 Headers: {response.headers}")
        return response.status_code == 202

    except Exception as e:
        log(f"❌ Erreur SendGrid : {e}")
        return False

def parse_iso8601(value):
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.datetime.fromisoformat(value)
    except Exception:
        return None

def load_mirakl_state():
    if not MIRAKL_STATE_FILE:
        return {}
    if os.path.exists(MIRAKL_STATE_FILE):
        try:
            with open(MIRAKL_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"⚠️ Impossible de lire {MIRAKL_STATE_FILE}: {e}")
    return {}

def save_mirakl_state(state):
    if not MIRAKL_STATE_FILE:
        return
    try:
        with open(MIRAKL_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"⚠️ Impossible d'écrire {MIRAKL_STATE_FILE}: {e}")

def fetch_mirakl_orders():
    if not MIRAKL_API_KEY:
        raise RuntimeError("MIRAKL_API_KEY non défini")

    base_url = f"{MIRAKL_API_BASE_URL.rstrip('/')}/api/orders"
    headers = {
        "Authorization": MIRAKL_API_KEY,
        "Accept": "application/json",
    }

    all_orders = []
    max_per_page = 100
    offset = 0

    while True:
        url = f"{base_url}?max={max_per_page}&offset={offset}"
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        payload = response.json()

        orders = payload.get("orders", [])
        total_count = payload.get("total_count", 0)

        all_orders.extend(orders)
        log(f"📦 Mirakl: {len(orders)} commande(s) récupérée(s) (offset={offset}, total={len(all_orders)}/{total_count})")

        if len(orders) < max_per_page or len(all_orders) >= total_count:
            break

        offset += max_per_page

    log(f"📦 Mirakl: {len(all_orders)} commande(s) récupérée(s) au total")
    return all_orders

def build_mirakl_order_summary(order):
    order_id = order.get("order_id")
    created = order.get("created_date")
    channel = order.get("channel", {})
    customer = order.get("customer", {})
    shipping = customer.get("shipping_address", {})
    line_summaries = []
    for line in order.get("order_lines", []):
        sku = line.get("offer_sku") or line.get("product_shop_sku")
        qty = line.get("quantity")
        title = line.get("product_title")
        line_summaries.append(f"- {sku} x{qty} · {title}")

    shipping_name = f"{shipping.get('firstname', '')} {shipping.get('lastname', '')}".strip()
    shipping_address = " | ".join(filter(None, [
        shipping_name,
        shipping.get("street_1"),
        shipping.get("street_2"),
        f"{shipping.get('zip_code', '')} {shipping.get('city', '')}".strip(),
        shipping.get("country_iso_code"),
        shipping.get("phone"),
    ]))

    return "\n".join([
        f"Commande : {order_id}",
        f"Créée le : {created}",
        f"Canal : {channel.get('label', channel.get('code', 'inconnu'))}",
        "Lignes :",
        *line_summaries,
        "",
        "Adresse livraison :",
        shipping_address or "(non communiquée)",
    ])

def process_order(customer_email, language_email, line_items, order_id=None):
    if not customer_email:
        return {"error": "Email manquant"}, 400

    language_email = language_email or "fr"

    if not line_items:
        return {"error": "Aucun produit trouvé"}, 400

    bundle_skus = {"B2C001_BUNDLE"}
    subscription_skus = {"FOOTBAR_GOLD_1_AN_BUNDLE"}  # Seulement FOOTBAR_GOLD_1_AN_BUNDLE doit être ignoré en présence du bundle
    order_sku_set = set()
    for item in line_items:
        sku_clean = (item.get("sku") or "").strip().upper()
        if sku_clean:
            order_sku_set.add(sku_clean)
    skip_subscription_items = bool(bundle_skus & order_sku_set)

    results = []
    total_keys_sent = 0
    skipped_skus = []

    for item in line_items:
        title = item.get("title", "")
        raw_sku = item.get("sku", "")
        sku = raw_sku.strip().upper()
        qty_raw = item.get("quantity", 0)
        try:
            qty = int(qty_raw)
        except Exception:
            qty = 0

        if skip_subscription_items and sku in subscription_skus:
            log(f"ℹ️ SKU {sku} ignoré car bundle présent dans la commande")
            continue

        if not sku:
            log(f"⚠️ SKU manquant pour item: {title}")
            continue
        if qty <= 0:
            log(f"⚠️ Quantité manquante pour SKU: {sku}")
            continue

        config = find_product_config_for_sku(sku)
        if not config:
            log(f"⚠️ SKU inconnu ignoré: {sku} (produit: {title})")
            skipped_skus.append(sku)
            continue

        for _ in range(qty):
            key = get_and_use_license_key_gsheet(
                customer_email,
                config["spreadsheet_id"],
                config["range_name"],
                order_id=order_id,
            )
            if not key:
                return {"error": f"Aucune clé disponible pour {sku}"}, 500

            # Pour les commandes Amazon, utiliser l'email simple
            if order_id:
                email_sent = send_amazon_simple_email(
                    customer_email,
                    key,
                    order_id,
                    language_code=language_email,
                )
            else:
                email_sent = send_email_with_template(
                    customer_email,
                    key,
                    language_email,
                    template_fr_override=config.get("template_fr"),
                    template_en_override=config.get("template_en"),
                )
            if not email_sent:
                return {"error": f"Échec d'envoi d'email pour {sku}"}, 500

            results.append({
                "sku": sku,
                "key": key,
                "quantity_sent": 1
            })
            total_keys_sent += 1

    if total_keys_sent == 0:
        return {
            "error": "Aucun produit configuré trouvé dans la commande",
            "skipped_skus": skipped_skus,
            "known_skus": list(PRODUCT_CONFIG.keys())
        }, 400

    response = {
        "message": f"{total_keys_sent} clé(s) envoyée(s)",
        "total_keys": total_keys_sent,
        "details": results
    }

    if skipped_skus:
        response["skipped_skus"] = skipped_skus
        response["message"] += f" ({len(skipped_skus)} SKU(s) ignoré(s))"

    return response, 200

def poll_mirakl_and_notify():
    try:
        orders = fetch_mirakl_orders()
    except Exception as exc:
        log(f"❌ Erreur Mirakl: {exc}")
        return {"error": str(exc)}, 500

    state = load_mirakl_state()
    last_seen_raw = state.get("last_seen_updated_at")
    last_seen_dt = parse_iso8601(last_seen_raw)
    processed_list = list(state.get("processed_order_ids", []))
    processed_ids = set(processed_list)

    new_orders = []
    max_seen_dt = last_seen_dt

    for order in orders:
        order_id = order.get("order_id")
        if not order_id:
            continue
        if order_id in processed_ids:
            continue
        order_updated = parse_iso8601(order.get("last_updated_date"))
        new_orders.append(order)
        if order_updated and (not max_seen_dt or order_updated > max_seen_dt):
            max_seen_dt = order_updated

    if not new_orders:
        log("ℹ️ Mirakl: aucune nouvelle commande")
        return {"message": "Aucune nouvelle commande Mirakl"}, 200

    notifications = []
    for order in new_orders:
        order_id = order.get("order_id")
        customer_email = order.get("customer_notification_email")
        customer = order.get("customer", {})
        locale = customer.get("locale") or order.get("channel", {}).get("code") or "fr"
        line_items = []
        for line in order.get("order_lines", []):
            line_items.append({
                "title": line.get("product_title"),
                "sku": line.get("offer_sku") or line.get("product_shop_sku"),
                "quantity": line.get("quantity", 0),
            })

        payload, status = process_order(customer_email, locale, line_items, order_id=order_id)
        success = status == 200
        notifications.append({
            "order_id": order_id,
            "status": status,
            "result": payload,
        })

        if success:
            if order_id and order_id not in processed_ids:
                processed_ids.add(order_id)
                processed_list.append(order_id)
        else:
            log(f"⚠️ Mirakl commande {order_id} non traitée ({status}): {payload}")

    state["processed_order_ids"] = processed_list[-200:]
    if max_seen_dt:
        state["last_seen_updated_at"] = max_seen_dt.isoformat()
    save_mirakl_state(state)

    log(f"✅ Mirakl: {len([n for n in notifications if n['status'] == 200])} commande(s) traitée(s)")
    return {
        "message": f"{len([n for n in notifications if n['status'] == 200])} commande(s) Mirakl notifiée(s)",
        "notifications": notifications,
    }, 200

# ========== Amazon SP-API ==========

def get_amazon_access_token():
    """Obtient un access token LWA à partir du refresh token"""
    if not AMAZON_LWA_CLIENT_ID or not AMAZON_LWA_CLIENT_SECRET or not AMAZON_LWA_REFRESH_TOKEN:
        raise RuntimeError("Credentials Amazon LWA manquants")
    
    url = "https://api.amazon.com/auth/o2/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": AMAZON_LWA_REFRESH_TOKEN,
        "client_id": AMAZON_LWA_CLIENT_ID,
        "client_secret": AMAZON_LWA_CLIENT_SECRET,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    
    response = requests.post(url, data=data, headers=headers, timeout=20)
    response.raise_for_status()
    result = response.json()
    access_token = result.get("access_token")
    if not access_token:
        raise RuntimeError("Access token non reçu dans la réponse LWA")
    return access_token

def load_amazon_state():
    """Charge l'état Amazon depuis le fichier"""
    if not AMAZON_STATE_FILE:
        return {}
    if os.path.exists(AMAZON_STATE_FILE):
        try:
            with open(AMAZON_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"⚠️ Impossible de lire {AMAZON_STATE_FILE}: {e}")
    return {}

def save_amazon_state(state):
    """Sauvegarde l'état Amazon dans le fichier"""
    if not AMAZON_STATE_FILE:
        return
    try:
        with open(AMAZON_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"⚠️ Impossible d'écrire {AMAZON_STATE_FILE}: {e}")

def _amazon_sp_api_query_string(params):
    """Construit la query string pour SP-API (paramètres simples ou listes)."""
    parts = []
    for k, v in params.items():
        if isinstance(v, (list, tuple)):
            for item in v:
                parts.append(f"{k}={quote(str(item), safe='')}")
        else:
            parts.append(f"{k}={quote(str(v), safe='')}")
    return "&".join(parts)


def call_amazon_sp_api(endpoint_path, access_token, params=None):
    """Appelle l'API Amazon SP-API via awscurl"""
    if not AMAZON_SP_API_ACCESS_KEY or not AMAZON_SP_API_SECRET_KEY:
        raise RuntimeError("Credentials AWS pour Amazon SP-API manquants")
    
    url = f"{AMAZON_SP_API_ENDPOINT.rstrip('/')}{endpoint_path}"
    if params:
        url = f"{url}?{_amazon_sp_api_query_string(params)}"
    
    cmd = [
        "awscurl",
        "--region", AMAZON_SP_API_REGION,
        "--service", "execute-api",
        "--access_key", AMAZON_SP_API_ACCESS_KEY,
        "--secret_key", AMAZON_SP_API_SECRET_KEY,
        "--request", "GET",
        "--header", f"x-amz-access-token: {access_token}",
        "--header", "Accept: application/json",
        url,
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        log(f"❌ Erreur awscurl: {e.stderr}")
        raise RuntimeError(f"Erreur lors de l'appel Amazon SP-API: {e.stderr}")
    except json.JSONDecodeError as e:
        log(f"❌ Erreur parsing JSON: {e}")
        raise RuntimeError(f"Réponse Amazon SP-API invalide: {e}")

# Orders API v2026-01-01 (searchOrders + getOrder avec buyerEmail et items)
AMAZON_ORDERS_VERSION = "2026-01-01"


def fetch_amazon_orders(access_token, created_after=None):
    """Récupère les commandes Amazon via searchOrders (Orders API v2026-01-01)."""
    if created_after is None:
        created_after = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if isinstance(created_after, datetime.datetime):
        created_after_str = created_after.isoformat().replace("+00:00", "Z")
    else:
        created_after_str = created_after

    # Doc SP-API : marketplaceIds et fulfillmentStatuses en format comma-separated (pas paramètre répété).
    marketplace_ids_str = ",".join(m.strip() for m in AMAZON_MARKETPLACE_IDS.split(",") if m.strip())
    params = {
        "marketplaceIds": marketplace_ids_str,
        "createdAfter": created_after_str,
        "fulfillmentStatuses": "UNSHIPPED,PARTIALLY_SHIPPED,SHIPPED",
        "fulfilledBy": ["MERCHANT"],
        "includedData": ["BUYER"],
        "maxResultsPerPage": 100,
    }
    all_orders = []
    pagination_token = None
    while True:
        if pagination_token:
            params["paginationToken"] = pagination_token
        response = call_amazon_sp_api(f"/orders/{AMAZON_ORDERS_VERSION}/orders", access_token, params)
        orders = response.get("orders", [])
        all_orders.extend(orders)
        pagination = response.get("pagination", {}) or {}
        pagination_token = pagination.get("nextToken")
        if not pagination_token:
            break

    log(f"📦 Amazon: {len(all_orders)} commande(s) récupérée(s) (searchOrders v{AMAZON_ORDERS_VERSION})")
    return all_orders


def get_amazon_order_v2026(access_token, order_id):
    """Récupère le détail d'une commande Amazon via getOrder (v2026-01-01), avec buyer et items."""
    params = {"includedData": ["BUYER"]}
    response = call_amazon_sp_api(
        f"/orders/{AMAZON_ORDERS_VERSION}/orders/{order_id}",
        access_token,
        params,
    )
    return response.get("order")


def _normalize_order_v2026_to_v0(order_v2026):
    """Convertit une commande au format Orders API v2026 vers le format v0 (pour réutilisation du code existant)."""
    if not order_v2026:
        return None
    order_id = order_v2026.get("orderId", "")
    created = order_v2026.get("createdTime", "")
    sales = order_v2026.get("salesChannel", {}) or {}
    buyer = order_v2026.get("buyer", {}) or {}
    recipient = order_v2026.get("recipient", {}) or {}
    delivery = (recipient.get("deliveryAddress") or {}) if isinstance(recipient, dict) else {}
    order_items = order_v2026.get("orderItems", [])

    normalized = {
        "AmazonOrderId": order_id,
        "PurchaseDate": created,
        "SalesChannel": sales.get("channelName", "Amazon"),
        "MarketplaceId": sales.get("marketplaceId", ""),
        "BuyerInfo": {"BuyerEmail": (buyer.get("buyerEmail") or "").strip()},
        "ShippingAddress": {
            "PostalCode": delivery.get("postalCode"),
            "City": delivery.get("city"),
            "CountryCode": delivery.get("countryCode"),
        },
    }
    items_v0 = []
    for it in order_items:
        product = it.get("product") or {}
        items_v0.append({
            "SellerSKU": product.get("sellerSku", ""),
            "ASIN": product.get("asin", ""),
            "Title": product.get("title", ""),
            "QuantityOrdered": it.get("quantityOrdered", 0),
        })
    normalized["_orderItems"] = items_v0
    return normalized


def fetch_amazon_order_items(access_token, order_id):
    """Récupère les items d'une commande Amazon (legacy v0). Utilisé en secours si besoin."""
    endpoint = f"/orders/v0/orders/{order_id}/orderItems"
    response = call_amazon_sp_api(endpoint, access_token)
    items = response.get("payload", {}).get("OrderItems", [])
    return items

def build_amazon_order_summary(order, items=None):
    """Construit un résumé de commande Amazon (order au format v0 ou normalisé v2026)."""
    order_id = order.get("AmazonOrderId")
    purchase_date = order.get("PurchaseDate")
    marketplace = order.get("SalesChannel", "Amazon")
    buyer_info = order.get("BuyerInfo", {})
    buyer_email = (buyer_info.get("BuyerEmail") or "").strip()
    shipping_address = order.get("ShippingAddress", {}) or {}
    # Items peuvent venir de l'appelant (v0) ou être attachés à la commande normalisée (_orderItems)
    use_items = items if items is not None else order.get("_orderItems")
    line_summaries = []
    if use_items:
        for item in use_items:
            sku = item.get("SellerSKU") or item.get("ASIN", "")
            qty = item.get("QuantityOrdered", 0)
            title = item.get("Title", "")
            line_summaries.append(f"- {sku} x{qty} · {title}")

    shipping_address_str = " | ".join(filter(None, [
        shipping_address.get("PostalCode"),
        shipping_address.get("City"),
        shipping_address.get("CountryCode"),
    ]))
    
    lines_to_join = [
        f"Commande : {order_id}",
        f"Créée le : {purchase_date}",
        f"Marketplace : {marketplace}",
        f"Email : {buyer_email}",
        "Lignes :",
    ]
    if line_summaries:
        lines_to_join.extend(line_summaries)
    else:
        lines_to_join.append("(items non récupérés)")
    lines_to_join.extend([
        "",
        "Adresse livraison :",
        shipping_address_str or "(non communiquée)",
    ])
    
    return "\n".join(lines_to_join)

def poll_amazon_and_notify():
    """Poll Amazon et envoie les notifications pour les nouvelles commandes"""
    try:
        access_token = get_amazon_access_token()
    except Exception as exc:
        log(f"❌ Erreur obtention access token Amazon: {exc}")
        return {"error": str(exc)}, 500
    
    state = load_amazon_state()
    last_seen_raw = state.get("last_seen_purchase_date")
    last_seen_dt = parse_iso8601(last_seen_raw)
    
    # Si on a une dernière date vue, on l'utilise, sinon on prend aujourd'hui
    if last_seen_dt:
        created_after = last_seen_dt
    else:
        # Première exécution : on prend les commandes des 7 derniers jours
        created_after = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    
    try:
        orders = fetch_amazon_orders(access_token, created_after)
    except Exception as exc:
        log(f"❌ Erreur récupération commandes Amazon: {exc}")
        return {"error": str(exc)}, 500
    
    processed_list = list(state.get("processed_order_ids", []))
    processed_ids = set(processed_list)

    # Normaliser les commandes v2026 vers format v0 et filtrer les nouvelles
    new_orders = []
    max_seen_dt = last_seen_dt
    for order_v2026 in orders:
        order = _normalize_order_v2026_to_v0(order_v2026)
        if not order:
            continue
        order_id = order.get("AmazonOrderId")
        if not order_id:
            continue
        if order_id in processed_ids:
            continue
        # Si l'email n'est pas présent (searchOrders peut ne pas l'inclure selon les rôles), on appelle getOrder
        if not (order.get("BuyerInfo") or {}).get("BuyerEmail"):
            try:
                full_order = get_amazon_order_v2026(access_token, order_id)
                if full_order:
                    order = _normalize_order_v2026_to_v0(full_order)
            except Exception as e:
                log(f"⚠️ Erreur getOrder pour {order_id}: {e}")
        purchase_date = parse_iso8601(order.get("PurchaseDate"))
        new_orders.append(order)
        if purchase_date and (not max_seen_dt or purchase_date > max_seen_dt):
            max_seen_dt = purchase_date

    if not new_orders:
        log("ℹ️ Amazon: aucune nouvelle commande")
        return {"message": "Aucune nouvelle commande Amazon"}, 200

    notifications = []
    for order in new_orders:
        order_id = order.get("AmazonOrderId")
        buyer_info = order.get("BuyerInfo", {}) or {}
        customer_email = (buyer_info.get("BuyerEmail") or "").strip()

        # Détection de la langue basée sur le marketplace
        marketplace_id = order.get("MarketplaceId", "")
        sales_channel = order.get("SalesChannel", "")
        language_email = "fr"  # par défaut
        if "DE" in sales_channel or marketplace_id == "A1PA6795UKMFR9":
            language_email = "de"
        elif "IT" in sales_channel or marketplace_id == "APJ6JRA9NG5V4":
            language_email = "it"
        elif "ES" in sales_channel or marketplace_id == "A1RKKUPIHCS9HS":
            language_email = "es"
        elif "NL" in sales_channel or marketplace_id == "A1805IZSGTT6HS":
            language_email = "nl"
        elif "BE" in sales_channel or marketplace_id == "AMEN7PMS3EDWL":
            language_email = "fr"  # Belgique = français par défaut

        # Items déjà dans la commande normalisée (v2026)
        order_items = order.get("_orderItems") or []
        line_items = []
        for item in order_items:
            sku = item.get("SellerSKU") or ""
            qty = item.get("QuantityOrdered", 0)
            title = item.get("Title", "")
            line_items.append({
                "title": title,
                "sku": sku,
                "quantity": qty,
            })

        if not line_items:
            log(f"⚠️ Amazon commande {order_id}: aucun item trouvé")
            notifications.append({
                "order_id": order_id,
                "status": 400,
                "result": {"error": "Aucun item trouvé dans la commande"},
            })
            continue

        if not customer_email:
            log(f"⚠️ Amazon commande {order_id}: email acheteur non disponible (getOrder v2026)")
            notifications.append({
                "order_id": order_id,
                "status": 400,
                "result": {"error": "Email manquant"},
            })
            continue

        payload, status = process_order(customer_email, language_email, line_items, order_id=order_id)
        success = status == 200
        notifications.append({
            "order_id": order_id,
            "status": status,
            "result": payload,
        })
        
        if success:
            if order_id and order_id not in processed_ids:
                processed_ids.add(order_id)
                processed_list.append(order_id)
        else:
            log(f"⚠️ Amazon commande {order_id} non traitée ({status}): {payload}")
    
    state["processed_order_ids"] = processed_list[-200:]  # Garder les 200 dernières
    if max_seen_dt:
        state["last_seen_purchase_date"] = max_seen_dt.isoformat()
    save_amazon_state(state)
    
    log(f"✅ Amazon: {len([n for n in notifications if n['status'] == 200])} commande(s) traitée(s)")
    return {
        "message": f"{len([n for n in notifications if n['status'] == 200])} commande(s) Amazon notifiée(s)",
        "notifications": notifications,
    }, 200

# 🔑 Fonction de récupération de clé
def get_and_use_license_key_gsheet(to_email, spreadsheet_id, range_name, order_id=None):
    values = read_keys(spreadsheet_id, range_name)

    # Première ligne = header, données à partir de l’indice 1
    header = values[0]
    data = values[1:]

    key_index = header.index('key')
    used_index = header.index('used')
    mail_index = header.index('mail')
    date_index = header.index('date')
    order_id_index = header.index('order_id') if 'order_id' in header else None

    selected_key = None

    for row in data:
        # Par sécurité, on étend la ligne au besoin
        while len(row) < len(header):
            row.append('')

        if row[used_index].lower() == 'false' and not selected_key:
            selected_key = row[key_index]
            row[used_index] = 'true'
            row[mail_index] = to_email
            row[date_index] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if order_id_index is not None:
                row[order_id_index] = order_id if order_id else ''
            break

    if not selected_key:
        return None

    # On réinjecte les données modifiées
    updated_values = [header] + data
    write_keys(spreadsheet_id, range_name, updated_values)

    return selected_key

SPREADSHEET_ID = '1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0'
RANGE_NAME = 'Feuille 1!A1:E'

# 📩 Webhook Shopify Flow
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw_data = request.data.decode("utf-8")
        log(f"📥 RAW body: {raw_data}")
        data = json.loads(raw_data)

        customer_email = data.get("email")
        language_email = data.get("language")
        line_items = data.get("line_items", [])

        payload, status = process_order(customer_email, language_email, line_items)
        return jsonify(payload), status

    except json.JSONDecodeError as e:
        log(f"❌ Erreur JSON: {e}")
        return jsonify({"error": "Format JSON invalide"}), 400

    except Exception as e:
        log(f"❌ Erreur webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/mirakl/poll", methods=["POST"])
def mirakl_poll():
    payload, status_code = poll_mirakl_and_notify()
    return jsonify(payload), status_code

@app.route("/amazon/poll", methods=["POST"])
def amazon_poll():
    payload, status_code = poll_amazon_and_notify()
    return jsonify(payload), status_code

INVEST_SPREADSHEET_ID = "10FhSKicoGo2327o2Vx4B2NBv-zzyh4UFF4B2gSu2slY"  # ex: '1x9vyp_TLr7NJ...'
INVEST_RANGE = "InvestIntents!A1"      

def append_row(spreadsheet_id, range_a1, row_values):
    service = get_sheets_service()
    body = {"values": [row_values]}
    return service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=range_a1,
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()

# --- CORS simple (autoriser footbar.com) ---
from flask import make_response

ALLOWED_ORIGIN = "https://footbar.com"  # ou ton domaine précis de boutique

@app.after_request
def add_cors_headers(resp):
    origin = request.headers.get("Origin", "")
    if origin and origin.startswith(ALLOWED_ORIGIN):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return resp

@app.route("/invest-intent", methods=["POST", "OPTIONS"])
def invest_intent():
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error":"JSON invalide"}), 400

    # Honeypot anti-bot
    if data.get("hp"):
        return jsonify({"message":"ok"}), 200

    first_name = (data.get("first_name") or "").strip()
    last_name  = (data.get("last_name") or "").strip()
    email      = (data.get("email") or "").strip()
    country    = (data.get("country") or "").strip()
    amount_range = (data.get("amount_range") or "").strip()
    consent    = bool(data.get("consent"))

    # Validations minimales
    if not first_name or not last_name:
        return jsonify({"error":"Prénom et nom requis"}), 400
    import re
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error":"Email invalide"}), 400
    if not country:
        return jsonify({"error":"Pays requis"}), 400
    if not consent:
        return jsonify({"error":"Consentement requis"}), 400
    if not amount_range:
        return jsonify({"error":"Tranche d'investissement requise"}), 400

    # UTM/referrer (si tu veux les ajouter plus tard côté front)
    utm_source   = (data.get("utm_source") or "")
    utm_medium   = (data.get("utm_medium") or "")
    utm_campaign = (data.get("utm_campaign") or "")
    page_url     = (data.get("page_url") or "")
    referrer     = (data.get("referrer") or "")

    # Append dans Google Sheets
    try:
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        row = [
            ts,
            first_name,
            last_name,
            email,
            country,
            amount_range,
            "TRUE" if consent else "FALSE",
            utm_source, utm_medium, utm_campaign,
            page_url, referrer,
        ]
        append_row(INVEST_SPREADSHEET_ID, INVEST_RANGE, row)
    except Exception as e:
        log(f"❌ Erreur append GSheet: {e}")
        return jsonify({"error":"Erreur d'enregistrement"}), 500

    return jsonify({"message":"Intent enregistrée"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
