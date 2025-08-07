from flask import Flask, request, jsonify
import logging
import csv
import datetime
import os
import sendgrid
from sendgrid.helpers.mail import Mail

# Config
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
FROM_EMAIL = "help@footbar.com"  # adresse expéditrice
logging.basicConfig(level=logging.INFO)
# Init
app = Flask(__name__)

# 📩 Fonction d'envoi d'email
def send_email(to_email, license_key):
    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=to_email,
        subject="Votre clé d'activation",
        html_content=f"""
        <p>Bonjour,</p>
        <p>Merci pour votre commande ! Voici votre clé d'activation :</p>
        <h2>{license_key}</h2>
        <p>À bientôt !</p>
        """
    )
    try:
        sg = sendgrid.SendGridAPIClient(SENDGRID_API_KEY)
        sg.send(message)
    except Exception as e:
        print("Erreur d'envoi d'email :", e)
        return False
    return True

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
        # 🔍 Force le parsing JSON même si Content-Type est incorrect
        data = request.get_json(force=True)

        # 🔎 Debug optionnel (supprime en prod)
        print("RAW request body:", request.data)
        print("Parsed JSON:", data)
        print("Headers:", dict(request.headers))

        customer_email = data.get("email")
        if not customer_email:
            return jsonify({"error": "Email manquant"}), 400

        # 🔑 Récupère une clé non utilisée
        key = get_and_use_license_key(customer_email)
        if not key:
            return jsonify({"error": "Aucune clé disponible"}), 500

        # ✉️ Envoie l'email
        email_sent = send_email(customer_email, key)
        if not email_sent:
            return jsonify({"error": "Échec d’envoi d’email"}), 500

        return jsonify({"message": "Clé envoyée", "key": key}), 200

    except Exception as e:
        print("Erreur webhook:", e)
        return jsonify({"error": str(e)}), 500
        
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
