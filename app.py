import os, json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, redirect, url_for
from dotenv import load_dotenv
import pandas as pd
from random import randint, choice

load_dotenv()

app = Flask(__name__)
LOG_DATEI = "webhook_logs.json"
LETZTE_DATEI = "letzte_alarme.json"
INTERVALLE_DATEI = "erkannte_intervalle.json"

@app.route("/")
def index():
    return redirect(url_for("dashboard"))

@app.route("/webhook", methods=["POST"])
def empfang():
    data = request.get_json()
    if data:
        data["timestamp"] = datetime.now().isoformat()
        speichere_webhook_daten(data)
    return jsonify({"status": "ok"})

def speichere_webhook_daten(payload):
    if not os.path.exists(LOG_DATEI):
        with open(LOG_DATEI, "w") as f:
            json.dump([], f)
    with open(LOG_DATEI, "r") as f:
        daten = json.load(f)
    daten.append(payload)
    with open(LOG_DATEI, "w") as f:
        json.dump(daten, f, indent=2)

@app.route("/dashboard")
def dashboard():
    if not os.path.exists(LOG_DATEI):
        return "Keine Daten vorhanden."

    with open(LOG_DATEI, "r") as f:
        daten = json.load(f)

    if not daten:
        return "Logdatei leer."

    df = pd.DataFrame(daten)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["monat"] = df["timestamp"].dt.strftime("%Y-%m")
    df["jahr"] = df["timestamp"].dt.year

    matrix = df.groupby(["symbol", "monat"]).size().unstack(fill_value=0).to_dict()

    # Letzte Alarme laden
    letzte = []
    if os.path.exists(LETZTE_DATEI):
        with open(LETZTE_DATEI, "r") as f:
            letzte = json.load(f)

    # Intervalldaten laden
    intervalle = []
    if os.path.exists(INTERVALLE_DATEI):
        with open(INTERVALLE_DATEI, "r") as f:
            intervalle = json.load(f)

    return render_template("dashboard.html", matrix=matrix, letzte=letzte, intervalle=intervalle)

@app.route("/generate-testdata")
def generate_testdata():
    symbole = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"]
    basiszeit = datetime.now() - timedelta(days=365)
    daten = []

    for _ in range(200):
        eintrag = {
            "symbol": choice(symbole),
            "timestamp": (basiszeit + timedelta(days=randint(0, 400))).isoformat(),
            "details": f"Alarm bei {randint(100, 90000)} USD"
        }
        daten.append(eintrag)

    with open(LOG_DATEI, "w") as f:
        json.dump(daten, f, indent=2)

    letzte = sorted(daten, key=lambda x: x["timestamp"], reverse=True)[:10]
    with open(LETZTE_DATEI, "w") as f:
        json.dump(letzte, f, indent=2)

    intervalle = [
        {"symbol": "BTCUSDT", "zeitraum": "2025-06-01 bis 2025-06-05", "anzahl": 6},
        {"symbol": "ETHUSDT", "zeitraum": "2025-05-12 bis 2025-05-14", "anzahl": 4}
    ]
    with open(INTERVALLE_DATEI, "w") as f:
        json.dump(intervalle, f, indent=2)

    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
