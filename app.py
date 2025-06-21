import os, json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

EMAIL_ABSENDER = os.getenv("EMAIL_ABSENDER")
EMAIL_PASSWORT = os.getenv("EMAIL_PASSWORT")
EMAIL_EMPFANGER = os.getenv("EMAIL_EMPFANGER")
LOG_DATEI = "webhook_logs.json"

app = Flask(__name__)

@app.route("/")
def index():
    return "OK - Service laeuft"

@app.route("/webhook", methods=["POST"])
def empfang():
    data = request.get_json()
    if data:
        data["timestamp"] = datetime.now().isoformat()
        speichere_webhook_daten(data)
    return jsonify({"status": "ok"})

@app.route("/dashboard")
def dashboard():
    if not os.path.exists(LOG_DATEI):
        return "Keine Daten vorhanden."
    with open(LOG_DATEI, "r") as f:
        daten = json.load(f)
    df = pd.DataFrame(daten)
    if df.empty or "timestamp" not in df.columns or "symbol" not in df.columns:
        return "Unzureichende Daten."
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["monat"] = df["timestamp"].dt.strftime("%Y-%m")
    matrix = df.groupby(["symbol", "monat"]).size().unstack(fill_value=0).to_dict()
    return render_template("dashboard.html", matrix=matrix)

def speichere_webhook_daten(payload):
    if not os.path.exists(LOG_DATEI):
        with open(LOG_DATEI, "w") as f:
            json.dump([], f)
    with open(LOG_DATEI, "r") as f:
        daten = json.load(f)
    daten.append(payload)
    with open(LOG_DATEI, "w") as f:
        json.dump(daten, f, indent=2)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
