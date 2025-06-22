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

    raw_data = request.get_json()
    now = datetime.now(MEZ)

    data = {
        "timestamp": raw_data.get("timestamp", now.isoformat()),
        "symbol": str(raw_data.get("symbol", "UNKNOWN")),
        "event": raw_data.get("event", "unspecified"),
        "price": raw_data.get("price", 0),
        "interval": raw_data.get("interval", "unspecified"),
        "trend": raw_data.get("trend", "neutral"),
        "nachricht": raw_data.get("nachricht", None)
    }

    data["valid"] = data["symbol"] != "UNKNOWN" and data["price"] > 0

    daten = []
    if os.path.exists(LOG_DATEI):
        with open(LOG_DATEI, "r") as f:
            try:
                daten = json.load(f)
            except json.JSONDecodeError:
                daten = []
    if isinstance(daten, dict):
        daten = [daten]
    elif not isinstance(daten, list):
        daten = []

    daten.append(data)
    with open(LOG_DATEI, "w") as f:
        json.dump(daten, f, indent=2)

    settings = {}
    if os.path.exists(SETTINGS_DATEI):
        with open(SETTINGS_DATEI, "r") as f:
            settings.update(json.load(f))

    df = pd.DataFrame(daten)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce', utc=True).dt.tz_convert(MEZ)

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
            try:
                daten = json.load(f)
            except json.JSONDecodeError:
                daten = []

    if isinstance(daten, dict):
        daten = [daten]
    elif not isinstance(daten, list):
        daten = []

    daten = [eintrag for eintrag in daten if isinstance(eintrag, dict)]

    try:
        df = pd.DataFrame(daten)
    except Exception as e:
        print("Fehler beim Erstellen des DataFrames:", e)
        df = pd.DataFrame([])

    for spalte in ["timestamp", "symbol", "event", "price", "interval", "trend", "nachricht", "valid"]:
        if spalte not in df.columns:
            df[spalte] = None

    if df.empty:
        jahre = [2025]
        aktuelles_jahr = int(year) if year and year.isdigit() else 2025
        monate = [datetime(2025, m, 1).strftime("%b") for m in range(1, 13)]
        matrix = {}
        letzte_ereignisse = []
        fehlerhafte_eintraege = []
    else:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce', utc=True).dt.tz_convert(MEZ)
        df["symbol"] = df["symbol"].astype(str)
        df["jahr"] = df["timestamp"].dt.year
        df["monat"] = df["timestamp"].dt.strftime("%b")

        jahre = sorted(df["jahr"].dropna().unique())
        aktuelles_jahr = int(year) if year and year.isdigit() else jahre[-1]
        df_jahr = df[df["jahr"] == aktuelles_jahr]

        monate = [datetime(2025, m, 1).strftime("%b") for m in range(1, 13)]
        matrix = {
            symbol: [df_jahr[(df_jahr["symbol"] == symbol) & (df_jahr["monat"] == monat)].shape[0] for monat in monate]
            for symbol in df_jahr["symbol"].dropna().unique()
        }

        letzte_ereignisse = df.sort_values("timestamp", ascending=False).head(10).to_dict("records")
        fehlerhafte_eintraege = df[df["valid"] != True].sort_values("timestamp", ascending=False).head(10).to_dict("records")

    einstellungen = {}
    if os.path.exists(SETTINGS_DATEI):
        with open(SETTINGS_DATEI, "r") as f:
            try:
                einstellungen = json.load(f)
            except Exception as e:
                print("Fehler beim Laden der Einstellungen:", e)
                einstellungen = {}

    return render_template("dashboard.html",
        matrix=matrix,
        monate=monate,
        monate_js=json.dumps(monate),
        verfuegbare_jahre=jahre,
        aktuelles_jahr=aktuelles_jahr,
        letzte_ereignisse=letzte_ereignisse,
        einstellungen=einstellungen,
        einstellungs_info="",
        fehlerhafte_eintraege=fehlerhafte_eintraege
    )

@app.route("/update-settings", methods=["POST"])
def update_settings():
    symbol = request.form.get("symbol", "").strip().upper()
    if not symbol:
        return redirect(url_for("dashboard"))

    dropdown_value = request.form.get("interval_hours_dropdown", "1")
    manual_value = request.form.get("interval_hours_manual", "").strip()
    try:
        interval_hours = int(manual_value) if manual_value else int(dropdown_value)
    except ValueError:
        interval_hours = 1

    max_alarms = int(request.form.get("max_alarms", 3))
    trend_richtung = request.form.get("trend_richtung", "neutral")
    force_overwrite = request.form.get("force_overwrite", "false") == "true"

    einstellungen = {}
    if os.path.exists(SETTINGS_DATEI):
        with open(SETTINGS_DATEI, "r") as f:
            einstellungen = json.load(f)

    key = f"global_{trend_richtung}_{symbol}"
    einstellungen[key] = {
        "symbol": symbol,
        "interval_hours": interval_hours,
        "max_alarms": max_alarms,
        "trend_richtung": trend_richtung
    }

    with open(SETTINGS_DATEI, "w") as f:
        json.dump(einstellungen, f, indent=2)

    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
