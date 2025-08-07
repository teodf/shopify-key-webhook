from flask import Flask, request, jsonify
import csv
import datetime
import os
import sendgrid
from sendgrid.helpers.mail import Mail

# Config
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
FROM_EMAIL = "help@footbar.com"  # adresse expéditrice

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
                row['email'] = to_email
                row['date'] = datetime.datetime.now().isoformat()
            keys.append(row)

    if not selected_key:
        return None  # plus de clé dispo

    with open('keys.csv', 'w', newline='') as csvfile:
        fieldnames = ['key', 'used', 'email', 'date']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(keys)

    return selected_key

# 📩 Webhook Shopify Flow
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    customer_email = data.get("email")
    line_items = data.get("line_items", [])

    # Vérifie que le produit spécifique est dans la commande
    TARGET_PRODUCT_ID = "10217393946967"  # Remplace par l’ID réel du produit
    found = any(str(item.get("product_id")) == TARGET_PRODUCT_ID for item in line_items)

    if not found:
        return jsonify({"message": "Produit non déclencheur"}), 200

    key = get_and_use_license_key(customer_email)
    if not key:
        return jsonify({"error": "Aucune clé disponible"}), 500

    email_sent = send_email(customer_email, key)
    if not email_sent:
        return jsonify({"error": "Échec d’envoi d’email"}), 500

    return jsonify({"message": "Clé envoyée", "key": key}), 200

# 🎉 Lancement local
if __name__ == "__main__":
    app.run(port=5000)
