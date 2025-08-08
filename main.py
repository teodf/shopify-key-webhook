from flask import Flask, request, jsonify
import logging
import csv
import datetime
import os
import sendgrid
import json
from sendgrid.helpers.mail import Mail

# Config
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
SENDGRID_TEMPLATE_ID = "d-da4295a9f558493a8b6988af60e501de"
FROM_EMAIL = "help@footbar.com"  # adresse expéditrice
logging.basicConfig(level=logging.INFO)
# Init
app = Flask(__name__)

# 📩 Fonction d'envoi d'email
def send_email_with_template(to_email, licence_key):
    try:
        message = Mail(
            from_email=("help@footbar.com", "Footbar"),
            to_emails=to_email
        )
        message.dynamic_template_data = {
            "licence_key": licence_key
        }
        message.template_id = SENDGRID_TEMPLATE_ID

        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(response.status_code)
        return True
    except Exception as e:
        print(f"Erreur SendGrid : {e}")
        return False

# 🔑 Fonction de récupération de clé
def get_and_use_license_key(to_email):
    keys = []
    selected_key = None

    with open('keys.csv', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['used'].lower() == 'false' and not selected_key:
                selected_key = row['key']
                row['used'] = 'true'
                row['mail'] = to_email
                row['date'] = datetime.datetime.now().isoformat()
            keys.append(row)

    if not selected_key:
        return None  # plus de clé dispo

    with open('keys.csv', 'w', newline='') as csvfile:
        fieldnames = ['key', 'used', 'mail', 'date']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(keys)

    return selected_key

# 📩 Webhook Shopify Flow
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        # 🔍 Force parsing du JSON brut, peu importe le Content-Type
        raw_data = request.data.decode("utf-8")
        data = json.loads(raw_data)

        print("RAW body:", raw_data)
        print("Parsed JSON:", data)

        customer_email = data.get("email")
        if not customer_email:
            return jsonify({"error": "Email manquant"}), 400

        # 🔑 Récupère une clé non utilisée
        key = get_and_use_license_key(customer_email)
        if not key:
            return jsonify({"error": "Aucune clé disponible"}), 500

        # ✉️ Envoie l'email
        email_sent = send_email_with_template(customer_email, key)
        if not email_sent:
            return jsonify({"error": "Échec d’envoi d’email"}), 500

        return jsonify({"message": "Clé envoyée", "key": key}), 200

    except json.JSONDecodeError as e:
        print("Erreur JSON:", e)
        return jsonify({"error": "Format JSON invalide"}), 400

    except Exception as e:
        print("Erreur webhook:", e)
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
