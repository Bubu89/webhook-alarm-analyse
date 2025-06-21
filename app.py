import os, json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, redirect, url_for
from dotenv import load_dotenv
import pandas as pd
import random
import smtplib
from email.mime.text import MIMEText

load_dotenv()

LOG_DATEI = "webhook_logs.json"
SETTINGS_DATEI = "settings.json"
EMAIL_ABSENDER = os.getenv("EMAIL_ABSENDER")
EMAIL_PASSWORT = os.getenv("EMAIL_PASSWORT")
EMAIL_EMPFANGER = os.getenv("EMAIL_EMPFANGER")

app = Flask(__name__)

def sende_email(betreff, inhalt):
    try:
        msg = MIMEText(inhalt)
        msg["Subject"] = betreff
        msg["From"] = EMAIL_ABSENDER
        msg["To"] = EMAIL_EMPFANGER

        server = smtplib.SMTP("mail.gmx.net", 587)
        server.starttls()
        server.login(EMAIL_ABSENDER, EMAIL_PASSWORT)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        print(f"E-Mail konnte nicht gesendet werden: {e}")

@app.route("/")
def home():
    return redirect("/dashboard")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if data:
        data["timestamp"] = datetime.now().isoformat()
        daten = []
        if os.path.exists(LOG_DATEI):
            with open(LOG_DATEI, "r") as f:
                daten = json.load(f)
        daten.append(data)
        with open(LOG_DATEI, "w") as f:
            json.dump(daten, f, indent=2)

        if os.path.exists(SETTINGS_DATEI):
            with open(SETTINGS_DATEI, "r") as f:
                einstellungen = json.load(f)

            df = pd.DataFrame(daten)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            zeitraum = datetime.now() - timedelta(hours=einstellungen.get("interval_hours", 1))
            gefiltert = df[(df["timestamp"] >= zeitraum) & (df["symbol"] == data["symbol"])]

            if len(gefiltert) >= einstellungen.get("max_alarms", 3):
                sende_email("Alarm: Häufung erkannt", f"Symbol: {data['symbol']} - {len(gefiltert)} Alarme innerhalb des Zeitfensters.")

    return jsonify({"status": "ok"})

@app.route("/dashboard")
def dashboard():
    year = request.args.get("year")
    if not os.path.exists(LOG_DATEI):
        return "Keine Daten vorhanden."

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

    einstellungen = {}
    if os.path.exists(SETTINGS_DATEI):
        with open(SETTINGS_DATEI, "r") as f:
            einstellungen = json.load(f)

    return render_template("dashboard.html",
        matrix=matrix,
        monate=monate,
        monate_js=json.dumps(monate),
        verfuegbare_jahre=jahre,
        aktuelles_jahr=aktuelles_jahr,
        letzte_ereignisse=letzte_ereignisse,
        einstellungen=einstellungen
    )

@app.route("/generate-testdata", methods=["GET", "POST"])
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
        "symbol_filter": request.form.get("symbol_filter"),
        "interval_hours": int(request.form.get("interval_hours", 1)),
        "threshold_percent": int(request.form.get("threshold_percent", 10)),
        "max_alarms": int(request.form.get("max_alarms", 3))
    }
    with open(SETTINGS_DATEI, "w") as f:
        json.dump(einstellungen, f, indent=2)
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
