import os, json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, redirect, url_for
from dotenv import load_dotenv
import pandas as pd
import random

load_dotenv()

LOG_DATEI = "webhook_logs.json"
SETTINGS_DATEI = "settings.json"

# Standard-Einstellungen, falls Datei nicht vorhanden
DEFAULT_SETTINGS = {
    "symbol_filter": "",
    "interval_hours": 1,
    "threshold_percent": 10,
    "max_alarms": 3
}

app = Flask(__name__)

@app.route("/")
def home():
    return redirect("/dashboard")

@app.route("/dashboard")
def dashboard():
    year = request.args.get("year")

    # Lade Daten
    if not os.path.exists(LOG_DATEI):
        daten = []
    else:
        with open(LOG_DATEI, "r") as f:
            daten = json.load(f)

    if not daten:
        return "Keine gespeicherten Alarme vorhanden."

    df = pd.DataFrame(daten)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["symbol"] = df["symbol"].astype(str)
    df["jahr"] = df["timestamp"].dt.year
    df["monat"] = df["timestamp"].dt.strftime("%b")

    jahre = sorted(df["jahr"].unique())
    aktuelles_jahr = int(year) if year and year.isdigit() else jahre[-1]
    df = df[df["jahr"] == aktuelles_jahr]

    monate = [datetime(2025, m, 1).strftime("%b") for m in range(1, 13)]
    matrix = {
        symbol: [df[(df["symbol"] == symbol) & (df["monat"] == monat)].shape[0] for monat in monate]
        for symbol in df["symbol"].unique()
    }

    letzte_ereignisse = df.sort_values("timestamp", ascending=False).head(10).to_dict("records")

    einstellungen = lade_settings()

    return render_template("dashboard.html",
        matrix=matrix,
        monate=monate,
        monate_js=json.dumps(monate),
        verfuegbare_jahre=jahre,
        aktuelles_jahr=aktuelles_jahr,
        letzte_ereignisse=letzte_ereignisse,
        einstellungen=einstellungen
    )

@app.route("/generate-testdata", methods=["POST"])
def generate_testdata():
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    now = datetime.now()
    daten = []
    for days_ago in range(365):
        for _ in range(random.randint(0, 3)):
            daten.append({
                "symbol": random.choice(symbols),
                "timestamp": (now - timedelta(days=days_ago)).isoformat(),
                "nachricht": random.choice(["Breakout", "Support", "New 52W High"])
            })
    with open(LOG_DATEI, "w") as f:
        json.dump(daten, f, indent=2)
    return redirect(url_for("dashboard"))

@app.route("/update-settings", methods=["POST"])
def update_settings():
    einstellungen = {
        "symbol_filter": request.form.get("symbol_filter", ""),
        "interval_hours": int(request.form.get("interval_hours", 1)),
        "threshold_percent": int(request.form.get("threshold_percent", 10)),
        "max_alarms": int(request.form.get("max_alarms", 3))
    }
    with open(SETTINGS_DATEI, "w") as f:
        json.dump(einstellungen, f, indent=2)
    return redirect(url_for("dashboard"))

def lade_settings():
    if not os.path.exists(SETTINGS_DATEI):
        return DEFAULT_SETTINGS
    try:
        with open(SETTINGS_DATEI, "r") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_SETTINGS

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
