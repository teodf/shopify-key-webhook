from flask import Flask, request, jsonify
import logging
import csv
import datetime
import os
import json
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# =====================
# Config
# =====================
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
SENDGRID_TEMPLATE_ID = "d-da4295a9f558493a8b6988af60e501de"
FROM_EMAIL = "help@footbar.com"

# Configuration du logging pour Render (flush immédiat)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True
)

# Init Flask
app = Flask(__name__)

# =====================
# 📩 Fonction d'envoi d'email
# =====================
def send_email_with_template(to_email, licence_key):
    try:
        logging.info(f"📨 Préparation de l'envoi à {to_email} avec clé {licence_key}")

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

        logging.info(f"✅ Email envoyé : status={response.status_code}")
        logging.info(f"Body: {response.body}")
        logging.info(f"Headers: {response.headers}")

        return True
    except Exception as e:
        logging.error(f"❌ Erreur SendGrid : {e}", exc_info=True)
        return False

# =====================
# 🔑 Fonction de récupération de clé
# =====================
def get_and_use_license_key(to_email):
    logging.info(f"🔍 Recherche d'une clé disponible pour {to_email}")
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
                logging.info(f"🔑 Clé trouvée : {selected_key}")
            keys.append(row)

    if not selected_key:
        logging.warning("⚠️ Plus de clé disponible")
        return None

    with open('keys.csv', 'w', newline='') as csvfile:
        fieldnames = ['key', 'used', 'mail', 'date']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(keys)

    logging.info("💾 Fichier keys.csv mis à jour")
    return selected_key

# =====================
# 📩 Webhook Shopify Flow
# =====================
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw_data = request.data.decode("utf-8")
        logging.info(f"📥 RAW body: {raw_data}")

        data = json.loads(raw_data)
        logging.info(f"📦 Parsed JSON: {data}")

        customer_email = data.get("email")
        if not customer_email:
            logging.error("❌ Email manquant dans la requête")
            return jsonify({"error": "Email manquant"}), 400

        # Récupère une clé
        key = get_and_use_license_key(customer_email)
        if not key:
            return jsonify({"error": "Aucune clé disponible"}), 500

        # Envoie l'email
        email_sent = send_email_with_template(customer_email, key)
        if not email_sent:
            return jsonify({"error": "Échec d’envoi d’email"}), 500

        return jsonify({"message": "Clé envoyée", "key": key}), 200

    except json.JSONDecodeError as e:
        logging.error(f"❌ Erreur JSON: {e}", exc_info=True)
        return jsonify({"error": "Format JSON invalide"}), 400

    except Exception as e:
        logging.error(f"❌ Erreur webhook: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# =====================
# 🚀 Lancement serveur
# =====================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"🚀 Démarrage serveur sur le port {port}")
    app.run(host="0.0.0.0", port=port)
