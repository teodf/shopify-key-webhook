from flask import Flask, request, jsonify
import logging
import csv
import datetime
import os
import json
import os.path
import pickle
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

# Logs Render (flush direct)
logging.basicConfig(level=logging.INFO, force=True)

def log(msg):
    print(f"[LOG] {msg}", flush=True)

app = Flask(__name__)

# Configuration par produit (routing via SKU)
PRODUCT_CONFIG = {
    "FOOTBAR_GOLD_1_AN": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Feuille 1!A1:D",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "B2C001": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Bundle!A1:D",
        "template_fr": "d-da4295a9f558493a8b6988af60e501de",
        "template_en": "d-0314abc9f83a4ab3bc9c3068b9b0e2a1",
    },
    "FOOTBAR_TEAM_1_MOIS": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Plateforme Coach 1 mois!A1:D",
        "template_fr": "A DEFINIR",
        "template_en": "A DEFINIR",
    },
    "FOOTBAR_TEAM_1_AN": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Plateforme Coach 1 an!A1:D",
        "template_fr": "A DEFINIR",
        "template_en": "A DEFINIR",
    },
    "FOOTBAR_TEAM_2_ANS": {
        "spreadsheet_id": "1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0",
        "range_name": "Plateforme Coach 2 ans!A1:D",
        "template_fr": "A DEFINIR",
        "template_en": "A DEFINIR",
    },
}

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

# 📩 Fonction d'envoi d'email
def send_email_with_template(to_email, licence_key, language_code, template_fr_override=None, template_en_override=None):
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
        message.dynamic_template_data = {
            "licence_key": licence_key
        }
        message.template_id = template_id

        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        log(f"📨 Réponse SendGrid: {response.status_code}")
        log(f"📨 Headers: {response.headers}")
        return response.status_code == 202

    except Exception as e:
        log(f"❌ Erreur SendGrid : {e}")
        return False

# 🔑 Fonction de récupération de clé
def get_and_use_license_key_gsheet(to_email, spreadsheet_id, range_name):
    values = read_keys(spreadsheet_id, range_name)

    # Première ligne = header, données à partir de l’indice 1
    header = values[0]
    data = values[1:]

    key_index = header.index('key')
    used_index = header.index('used')
    mail_index = header.index('mail')
    date_index = header.index('date')

    selected_key = None

    for row in data:
        # Par sécurité, on étend la ligne au besoin
        while len(row) < len(header):
            row.append('')

        if row[used_index].lower() == 'false' and not selected_key:
            selected_key = row[key_index]
            row[used_index] = 'true'
            row[mail_index] = to_email
            row[date_index] = datetime.datetime.now().isoformat()
            break

    if not selected_key:
        return None

    # On réinjecte les données modifiées
    updated_values = [header] + data
    write_keys(spreadsheet_id, range_name, updated_values)

    return selected_key

SPREADSHEET_ID = '1x9vyp_TLr7NJSt6n-2qnXF43-MY1fG67ghu0B425or0'
RANGE_NAME = 'Feuille 1!A1:D'

# 📩 Webhook Shopify Flow
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw_data = request.data.decode("utf-8")
        log(f"📥 RAW body: {raw_data}")
        data = json.loads(raw_data)

        customer_email = data.get("email")
        if not customer_email:
            return jsonify({"error": "Email manquant"}), 400

        language_email = data.get("language")
        if not language_email:
            return jsonify({"error": "Langue manquante"}), 400

        line_items = data.get("line_items", [])
        if not line_items:
            return jsonify({"error": "Aucun produit trouvé"}), 400

        # On sélectionne le premier item avec un SKU connu (configuré)
        selected_config = None
        selected_sku = None
        for item in line_items:
            title = item.get("title", "")
            sku = item.get("sku", "")
            qty = int(item.get("quantity", 0))
            if not sku:
                return jsonify({"error": "SKU manquant"}), 400
            if not qty:
                return jsonify({"error": "Quantité manquante"}), 400
            if sku in PRODUCT_CONFIG and not selected_config:
                selected_config = PRODUCT_CONFIG[sku]
                selected_sku = sku

        if not selected_config:
            return jsonify({"error": "SKU inconnu ou non configuré", "known_skus": list(PRODUCT_CONFIG.keys())}), 400

        key = get_and_use_license_key_gsheet(
            customer_email,
            selected_config["spreadsheet_id"],
            selected_config["range_name"],
        )
        if not key:
            return jsonify({"error": "Aucune clé disponible"}), 500

        email_sent = send_email_with_template(
            customer_email,
            key,
            language_email,
            template_fr_override=selected_config.get("template_fr"),
            template_en_override=selected_config.get("template_en"),
        )
        if not email_sent:
            return jsonify({"error": "Échec d’envoi d’email"}), 500

        return jsonify({"message": "Clé envoyée", "key": key, "sku": selected_sku}), 200

    except json.JSONDecodeError as e:
        log(f"❌ Erreur JSON: {e}")
        return jsonify({"error": "Format JSON invalide"}), 400

    except Exception as e:
        log(f"❌ Erreur webhook: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
