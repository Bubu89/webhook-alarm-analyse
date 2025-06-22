import os, json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, redirect, url_for
from dotenv import load_dotenv
import pandas as pd
import smtplib
from email.mime.text import MIMEText
import pytz
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from collections import defaultdict

from flask import send_file
from pathlib import Path

@app.route("/export_csv")
def export_csv():
    conn = engine.connect()
    df = pd.read_sql("SELECT * FROM webhook_alarme", conn)
    conn.close()
    Path("static").mkdir(exist_ok=True)
    export_path = "static/webhook_export.csv"
    df.to_csv(export_path, index=False)
    return send_file(export_path, as_attachment=True, download_name="webhook_export.csv")

# === SETUP ===
load_dotenv()
MEZ = pytz.timezone("Europe/Vienna")
LOG_DATEI = "webhook_logs.json"
BACKUP_DATEI = f"webhook_logs_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
SETTINGS_DATEI = "settings.json"
EMAIL_ABSENDER = os.getenv("EMAIL_ABSENDER")
EMAIL_PASSWORT = os.getenv("EMAIL_PASSWORT")
EMAIL_EMPFANGER = os.getenv("EMAIL_EMPFANGER")
app = Flask(__name__)

# === SQLite ===
engine = create_engine("sqlite:///webhook.db")
Base = declarative_base()
Session = sessionmaker(bind=engine)
session = Session()

class WebhookAlarm(Base):
    __tablename__ = "webhook_alarme"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, nullable=False)
    symbol = Column(String, nullable=False)
    event = Column(String)
    price = Column(Float)
    interval = Column(String)
    trend = Column(String)
    nachricht = Column(String)
    valid = Column(Boolean)

Base.metadata.create_all(engine)

# === Backup alte JSON-Datei (falls vorhanden) ===
if os.path.exists(LOG_DATEI):
    os.rename(LOG_DATEI, BACKUP_DATEI)

# === Funktionen ===
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
        print("E-Mail erfolgreich gesendet")
    except Exception as e:
        print(f"E-Mail konnte nicht gesendet werden: {e}")

def erzeuge_stunden_daten(df: pd.DataFrame) -> list[dict]:
    df = df[df["trend"].isin(["bullish", "bearish"])].copy()
    df["stunde"] = df["timestamp"].dt.strftime("%H")
    gruppiert = df.groupby(["stunde", "symbol", "trend"]).size().reset_index(name="anzahl")
    struktur = defaultdict(dict)
    for _, row in gruppiert.iterrows():
        stunde = row["stunde"]
        key = f"{row['symbol']}_{row['trend']}"
        struktur[stunde][key] = row["anzahl"]
    result = []
    for h in range(24):
        stunde = f"{h:02d}"
        eintrag = {"stunde": stunde}
        if stunde in struktur:
            eintrag.update(struktur[stunde])
        result.append(eintrag)
    return result

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
        "nachricht": raw_data.get("nachricht", None),
        "valid": raw_data.get("symbol", "UNKNOWN") != "UNKNOWN" and raw_data.get("price", 0) > 0
    }

    try:
        alarm = WebhookAlarm(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            symbol=data["symbol"],
            event=data["event"],
            price=float(data["price"]),
            interval=data["interval"],
            trend=data["trend"],
            nachricht=data["nachricht"],
            valid=data["valid"]
        )
        session.add(alarm)
        session.commit()
    except SQLAlchemyError as e:
        session.rollback()
        return jsonify({"error": f"DB-Fehler: {e}"}), 500

    # Alarmprüfung
    settings = {}
    if os.path.exists(SETTINGS_DATEI):
        with open(SETTINGS_DATEI, "r") as f:
            settings.update(json.load(f))

    symbol = data["symbol"]
    trend = data["trend"].lower()
    key_symbol = f"global_{trend}_{symbol}"
    key_all = f"global_{trend}"
    config = settings.get(key_symbol) or settings.get(key_all)
    if not config:
        return jsonify({"status": "keine passenden Einstellungen gefunden"})

    zeitraum = now - timedelta(hours=config.get("interval_hours", 6))
    try:
        recent = session.query(WebhookAlarm).filter(
            WebhookAlarm.symbol == symbol,
            WebhookAlarm.timestamp >= zeitraum
        ).all()
        if config.get("max_alarms", 3) == 1 or len(recent) >= config.get("max_alarms", 3):
            sende_email(f"Alarm: {symbol}", f"{len(recent)} Alarme in {config['interval_hours']}h ({trend})")
    except:
        pass

    return jsonify({"status": "ok"})

@app.route("/dashboard")
def dashboard():
    year = request.args.get("year")
    try:
        daten = session.query(WebhookAlarm).all()
        daten = [d.__dict__ for d in daten]
        for d in daten:
            d.pop("_sa_instance_state", None)
    except:
        daten = []

    df = pd.DataFrame(daten)
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
        tages_daten = []
        stunden_daten = []
    else:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce').dt.tz_localize("UTC").dt.tz_convert(MEZ)
        df["symbol"] = df["symbol"].astype(str)
        df["jahr"] = df["timestamp"].dt.year
        df["monat"] = df["timestamp"].dt.strftime("%b")
        df["tag"] = df["timestamp"].dt.date
        df["uhrzeit"] = df["timestamp"].dt.strftime("%H:%M")
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
        sortierte_paare = ["BTC", "ETH"] + sorted([s for s in df["symbol"].unique() if s not in ["BTC", "ETH"]])
        df["symbol"] = pd.Categorical(df["symbol"], categories=sortierte_paare, ordered=True)
        tages_daten = df.groupby(["tag", "symbol"]).size().unstack(fill_value=0).sort_index().reset_index()
        stunden_daten = erzeuge_stunden_daten(df)

    einstellungen = {}
    if os.path.exists(SETTINGS_DATEI):
        with open(SETTINGS_DATEI, "r") as f:
            try:
                einstellungen = json.load(f)
            except:
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
        fehlerhafte_eintraege=fehlerhafte_eintraege,
        tages_daten=tages_daten.to_dict(orient="records") if isinstance(tages_daten, pd.DataFrame) else tages_daten,
        stunden_daten=stunden_daten
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
    einstellungen = {}
    if os.path.exists(SETTINGS_DATEI):
        with open(SETTINGS_DATEI, "r") as f:
            einstellungen = json.load(f)
    key = f"global_{trend_richtung}_{symbol}" if symbol != "ALL" else f"global_{trend_richtung}"
    einstellungen[key] = {
        "symbol": symbol,
        "interval_hours": interval_hours,
        "max_alarms": max_alarms,
        "trend_richtung": trend_richtung
    }
    with open(SETTINGS_DATEI, "w") as f:
        json.dump(einstellungen, f, indent=2)
    return redirect(url_for("dashboard"))

@app.route("/delete-multiple-settings", methods=["POST"])
def delete_multiple_settings():
    keys_to_delete = request.form.getlist("delete_keys")
    if not keys_to_delete:
        return redirect(url_for("dashboard"))
    einstellungen = {}
    if os.path.exists(SETTINGS_DATEI):
        with open(SETTINGS_DATEI, "r") as f:
            try:
                einstellungen = json.load(f)
            except:
                einstellungen = {}
    for key in keys_to_delete:
        if key in einstellungen:
            del einstellungen[key]
    with open(SETTINGS_DATEI, "w") as f:
        json.dump(einstellungen, f, indent=2)
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
