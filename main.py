from flask import Flask, request, jsonify
import logging
import csv
import datetime
import os
import json
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from googleapiclient.discovery import build
from google.oauth2 import service_account

# Permissions demandées : lecture + écriture sur Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'credentials.json'

# Config
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
SENDGRID_TEMPLATE_ID = "d-da4295a9f558493a8b6988af60e501de"  # ID du modèle d'email SendGrid
FROM_EMAIL = "help@footbar.com"  # adresse expéditrice

# Logs Render (flush direct)
logging.basicConfig(level=logging.INFO, force=True)

def log(msg):
    print(f"[LOG] {msg}", flush=True)

app = Flask(__name__)

def get_sheets_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build('sheets', 'v4', credentials=creds)
    return service

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
def send_email_with_template(to_email, licence_key):
    try:
        log(f"📤 Envoi email à {to_email} avec clé {licence_key}")
        message = Mail(
            from_email=(FROM_EMAIL, "Footbar"),
            to_emails=to_email
        )
        message.dynamic_template_data = {
            "licence_key": licence_key
        }
        message.template_id = SENDGRID_TEMPLATE_ID

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

        key = get_and_use_license_key_gsheet(customer_email, SPREADSHEET_ID, RANGE_NAME)
        if not key:
            return jsonify({"error": "Aucune clé disponible"}), 500

        email_sent = send_email_with_template(customer_email, key)
        if not email_sent:
            return jsonify({"error": "Échec d’envoi d’email"}), 500

        return jsonify({"message": "Clé envoyée", "key": key}), 200

    except json.JSONDecodeError as e:
        log(f"❌ Erreur JSON: {e}")
        return jsonify({"error": "Format JSON invalide"}), 400

    except Exception as e:
        log(f"❌ Erreur webhook: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
