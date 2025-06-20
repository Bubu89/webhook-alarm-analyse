from flask import Flask, request, jsonify
from datetime import datetime
import json
import os
import smtplib
from email.message import EmailMessage

# ==== KONFIGURATION ====
EMAIL_ABSENDER = "yo.chris@gmx.at"
EMAIL_EMPFÄNGER = "yo.chris@gmx.at"  # Kann auch andere Adresse sein
APP_PASSWORT = "kY3N&CyLNPd&iZp2"    # Dein GMX.at Passwort

LOG_FILE = "webhook_logs.json"
app = Flask(__name__)

# ==== MAIL-VERSAND (nur GMX-SMTP!) ====
def sende_alarm_email(betreff, inhalt):
    msg = EmailMessage()
    msg.set_content(inhalt)
    msg["Subject"] = betreff
    msg["From"] = EMAIL_ABSENDER
    msg["To"] = EMAIL_EMPFÄNGER

import smtplib

try:
    with smtplib.SMTP_SSL("mail.gmx.net", 465) as smtp:
        smtp.login("yo.chris@gmx.at", "kY3N&CyLNPd&iZp2")
    print("✅ Login erfolgreich")
except Exception as e:
    print("❌ Login fehlgeschlagen:", e)


# ==== WEBHOOK-EMPFANG ====
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    data["timestamp"] = datetime.utcnow().isoformat()

    # JSON-Logdatei speichern
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            json.dump([], f)

    with open(LOG_FILE, "r+") as f:
        logs = json.load(f)
        logs.append(data)
        f.seek(0)
        json.dump(logs, f, indent=2)

    print("📨 Alarm empfangen:", data)

    # Inhalt der E-Mail
    inhalt = f"""
    📩 TradingView-Alarm empfangen!

    Symbol: {data.get('symbol', 'Unbekannt')}
    Intervall: {data.get('interval', 'Unbekannt')}
    Preis: {data.get('price', 'Unbekannt')}
    Ereignis: {data.get('event', 'Unbekannt')}
    Zeitpunkt (UTC): {data['timestamp']}
    """

    sende_alarm_email("🚨 TradingView Webhook-Alarm", inhalt)

    return jsonify({"status": "Webhook empfangen & E-Mail gesendet"}), 200

# ==== SERVER START ====
if __name__ == "__main__":
    app.run(port=5000)
