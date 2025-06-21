import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

EMAIL_ABSENDER = os.getenv("EMAIL_ABSENDER")
EMAIL_PASSWORT = os.getenv("EMAIL_PASSWORT")
EMAIL_EMPFANGER = os.getenv("EMAIL_EMPFANGER")
LOG_DATEI = "webhook_logs.json"

app = Flask(__name__, template_folder=".")

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
    if not daten:
        return "Logdatei leer."
    df = pd.DataFrame(daten)
    if df.empty or "timestamp" not in df.columns or "symbol" not in df.columns:
        return "Unzureichende Daten."

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["monat"] = df["timestamp"].dt.to_period("M").astype(str)

    matrix = df.groupby(["symbol", "monat"]).size().unstack(fill_value=0)

    return render_template_string(lade_dashboard_template(), table_html=matrix.to_html(classes="table", border=0))

def speichere_webhook_daten(payload):
    if not os.path.exists(LOG_DATEI):
        with open(LOG_DATEI, "w") as f:
            json.dump([], f)
    with open(LOG_DATEI, "r") as f:
        daten = json.load(f)
    daten.append(payload)
    with open(LOG_DATEI, "w") as f:
        json.dump(daten, f, indent=2)

def lade_dashboard_template():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Webhook Dashboard</title>
        <style>
            body { font-family: sans-serif; background: #f9f9f9; padding: 20px; }
            h1 { color: #333; }
            .table { border-collapse: collapse; width: 100%; background: white; }
            .table th, .table td { border: 1px solid #ccc; padding: 8px; text-align: center; }
            .table th { background: #eee; }
        </style>
    </head>
    <body>
        <h1>Webhook Dashboard</h1>
        {{ table_html|safe }}
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
