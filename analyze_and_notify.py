import os
import json
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from threading import Timer
from dotenv import load_dotenv

# === ENV-VARIABLEN LADEN (für lokale Nutzung mit .env) ===
load_dotenv()

# === KONFIGURATION AUS ENV ===
EMAIL_ABSENDER   = os.getenv("EMAIL_ABSENDER")
EMAIL_PASSWORT   = os.getenv("EMAIL_PASSWORT")
EMAIL_EMPFÄNGER  = os.getenv("EMAIL_EMPFÄNGER")
LOG_DATEI        = "webhook_logs.json"

app = Flask(__name__)

def speichere_webhook_daten(payload):
    if not os.path.exists(LOG_DATEI):
        with open(LOG_DATEI, "w") as f:
            json.dump([], f)

    with open(LOG_DATEI, "r") as f:
        daten = json.load(f)

    daten.append(payload)

    with open(LOG_DATEI, "w") as f:
        json.dump(daten, f, indent=2)

def sende_email(symbol, zeiten):
    try:
        msg = EmailMessage()
        msg["Subject"] = f"🚨 Trend-Alarm: {symbol} mit 3 Alarme"
        msg["From"] = EMAIL_ABSENDER
        msg["To"] = EMAIL_EMPFÄNGER
        msg.set_content("Alarme innerhalb 12h in niederen Zeitintervallen:\n" + '\n'.join(str(t) for t in zeiten))

        with smtplib.SMTP("mail.gmx.net", 587) as server:
            server.starttls()
            server.login(EMAIL_ABSENDER, EMAIL_PASSWORT)
            server.send_message(msg)
    except Exception as e:
        print("Fehler beim Mailversand:", e)

def analysiere_und_benachrichtige():
    if not os.path.exists(LOG_DATEI):
        return

    with open(LOG_DATEI, "r") as f:
        daten = json.load(f)

    jetzt = datetime.now()
    niedere_zeitfenster = ["30min", "1h", "2h"]
    intervall = timedelta(hours=12)

    gruppiert = {}
    for eintrag in daten:
        symbol = eintrag.get("symbol")
        interv = eintrag.get("interval")
        zeitstempel = eintrag.get("timestamp")
        if symbol and interv and zeitstempel and interv in niedere_zeitfenster:
            try:
                zeit = datetime.fromisoformat(zeitstempel)
                gruppiert.setdefault(symbol, []).append(zeit)
            except Exception as e:
                print("Ungültiger Zeitstempel:", zeitstempel)

    for symbol, zeiten in gruppiert.items():
        zeiten = [z for z in zeiten if jetzt - z <= intervall]
        if len(zeiten) >= 3:
            sende_email(symbol, zeiten)

    # Nächste Ausführung in 10 Minuten
    Timer(600, analysiere_und_benachrichtige).start()

@app.route("/webhook", methods=["POST"])
def empfang():
    data = request.get_json()
    if data:
        data["timestamp"] = datetime.now().isoformat()
        speichere_webhook_daten(data)
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    analysiere_und_benachrichtige()
    app.run(host="0.0.0.0", port=port)
