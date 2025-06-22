import os, json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, redirect, url_for
from dotenv import load_dotenv
import pandas as pd
import smtplib
from email.mime.text import MIMEText
import pytz

load_dotenv()

LOG_DATEI = "webhook_logs.json"
SETTINGS_DATEI = "settings.json"
EMAIL_ABSENDER = os.getenv("EMAIL_ABSENDER")
EMAIL_PASSWORT = os.getenv("EMAIL_PASSWORT")
EMAIL_EMPFANGER = os.getenv("EMAIL_EMPFANGER")

MEZ = pytz.timezone("Europe/Vienna")
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
    if not request.is_json:
        return jsonify({"error": "Content-Type muss application/json sein"}), 415

    data = request.get_json()
    if not data:
        return jsonify({"error": "Keine gültigen JSON-Daten erhalten"}), 400

    data["timestamp"] = datetime.now(MEZ).isoformat()
    daten = []
    if os.path.exists(LOG_DATEI):
        with open(LOG_DATEI, "r") as f:
            daten = json.load(f)
    daten.append(data)
    with open(LOG_DATEI, "w") as f:
        json.dump(daten, f, indent=2)

    settings = {}
    if os.path.exists(SETTINGS_DATEI):
        with open(SETTINGS_DATEI, "r") as f:
            settings.update(json.load(f))

    df = pd.DataFrame(daten)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format='mixed', errors='coerce')
    df["timestamp"] = df["timestamp"].dt.tz_convert(MEZ)

    symbol = data.get("symbol")
    trend = data.get("trend", "neutral").lower()
    key = f"global_{trend}_{symbol}" if f"global_{trend}_{symbol}" in settings else f"global_{trend}"

    config = settings.get(key)
    if not config:
        return jsonify({"status": "keine passenden Einstellungen gefunden"})

    zeitraum = datetime.now(MEZ) - timedelta(hours=config.get("interval_hours", 6))
    symbol_df = df[(df["symbol"] == symbol) & (df["timestamp"] >= zeitraum)]

    if len(symbol_df) >= config.get("max_alarms", 3):
        sende_email(f"Alarm: {symbol}", f"{len(symbol_df)} Alarme in {config['interval_hours']}h ({trend})")

    return jsonify({"status": "ok"})

@app.route("/dashboard")
def dashboard():
    year = request.args.get("year")
    daten = []
    if os.path.exists(LOG_DATEI):
        with open(LOG_DATEI, "r") as f:
            daten = json.load(f)

    df = pd.DataFrame(daten) if daten else pd.DataFrame(columns=["timestamp", "symbol", "event", "price", "interval"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], format='mixed', errors='coerce')
    df["timestamp"] = df["timestamp"].dt.tz_convert(MEZ)
    df["symbol"] = df["symbol"].astype(str)
    df["jahr"] = df["timestamp"].dt.year
    df["monat"] = df["timestamp"].dt.strftime("%b")

    jahre = sorted(df["jahr"].unique()) if not df.empty else [2025]
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
        einstellungen=einstellungen,
        einstellungs_info=""
    )

@app.route("/update-settings", methods=["POST"])
def update_settings():
    trend = request.form.get("trend_richtung", "neutral").lower()
    if trend not in ["bullish", "bearish"]:
        trend = "neutral"

    symbol = request.form.get("symbol", "global").strip().upper()
    key = f"global_{trend}_{symbol}" if symbol != "GLOBAL" else f"global_{trend}"

    dropdown_value = request.form.get("interval_hours_dropdown", "6")
    manual_value = request.form.get("interval_hours_manual", "").strip()
    try:
        interval_hours = int(manual_value) if manual_value else int(dropdown_value)
    except ValueError:
        interval_hours = 6

    try:
        max_alarms = int(request.form.get("max_alarms", 3))
    except ValueError:
        max_alarms = 3

    settings = {}
    if os.path.exists(SETTINGS_DATEI):
        with open(SETTINGS_DATEI, "r") as f:
            settings.update(json.load(f))

    if key not in settings:
        settings[key] = {}

    settings[key].update({
        "interval_hours": interval_hours,
        "max_alarms": max_alarms
    })

    with open(SETTINGS_DATEI, "w") as f:
        json.dump(settings, f, indent=2)

    return redirect(url_for("dashboard"))

@app.route("/delete-setting", methods=["POST"])
def delete_setting():
    key = request.form.get("key")
    if not key:
        return redirect("/dashboard")

    try:
        with open(SETTINGS_DATEI, "r", encoding="utf-8") as f:
            einstellungen = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        einstellungen = {}

    if key in einstellungen:
        del einstellungen[key]
        with open(SETTINGS_DATEI, "w", encoding="utf-8") as f:
            json.dump(einstellungen, f, indent=4)

    return redirect("/dashboard")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
