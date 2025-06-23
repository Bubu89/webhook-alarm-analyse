import os, json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, redirect, url_for
from dotenv import load_dotenv
import pandas as pd
import smtplib
from email.mime.text import MIMEText
import pytz
from collections import defaultdict

load_dotenv()

LOG_DATEI = "webhook_logs.json"
SETTINGS_DATEI = "settings.json"
EMAIL_ABSENDER = os.getenv("EMAIL_ABSENDER")
EMAIL_PASSWORT = os.getenv("EMAIL_PASSWORT")
EMAIL_EMPFANGER = os.getenv("EMAIL_EMPFANGER")

MEZ = pytz.timezone("Europe/Vienna")
app = Flask(__name__)

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

def erzeuge_trend_aggregat_daten(df: pd.DataFrame) -> list[dict]:
    if "timestamp" not in df.columns or "symbol" not in df.columns:
        return []

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce', utc=True)
    df = df[df["timestamp"].notna()]
    df["stunde"] = df["timestamp"].dt.strftime("%H")

    df = df[df["trend"].isin(["bullish", "bearish", "neutral"])]
    gruppiert = df.groupby(["stunde", "symbol", "trend"], observed=False).size().reset_index(name="anzahl")

    # Hilfsstruktur zur Aggregation
    aggregation = {}

    for _, row in gruppiert.iterrows():
        stunde = int(row["stunde"])
        symbol = row["symbol"]
        trend = row["trend"]
        anzahl = row["anzahl"]

        key = (stunde, symbol)
        if key not in aggregation:
            aggregation[key] = {"bullish": 0, "bearish": 0, "neutral": 0}

        aggregation[key][trend] += anzahl

    # Ergebnisstruktur
    result = []
    for (stunde, symbol), werte in aggregation.items():
        score = werte["bullish"] - werte["bearish"]
        farbe = "green" if score > 0 else "red" if score < 0 else "#888"

        result.append({
            "stunde": stunde,
            "symbol": symbol,
            "bullish": werte["bullish"],
            "bearish": werte["bearish"],
            "neutral": werte["neutral"],
            "score": score,
            "farbe": farbe
        })

    return result



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
        "timestamp": now.isoformat(),
        "symbol": str(raw_data.get("symbol", "UNKNOWN")),
        "event": raw_data.get("event", "unspecified"),
        "price": raw_data.get("price", 0),
        "interval": raw_data.get("interval", "unspecified"),
        "trend": raw_data.get("trend") or None,
        "nachricht": raw_data.get("nachricht", None)
    }

    if data["trend"] not in ["bullish", "bearish"]:
        return jsonify({"status": "Trend nicht ausgewertet – neutral oder leer"}), 200

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
    df_signal = df.copy()

    symbol = data.get("symbol")
    trend = data.get("trend", "neutral").lower()

    key_symbol = f"global_{trend}_{symbol}"
    key_all = f"global_{trend}"
    config = settings.get(key_symbol) or settings.get(key_all)

    if not config:
        return jsonify({"status": "keine passenden Einstellungen gefunden"})

    zeitraum = datetime.now(MEZ) - timedelta(hours=config.get("interval_hours", 6))
    symbol_df = df[(df["symbol"] == symbol) & (df["timestamp"] >= zeitraum)]
    symbol_df = symbol_df[symbol_df["timestamp"] < data["timestamp"]]  # ⛔ aktuelles nicht mitrechnen

    if config.get("max_alarms", 3) == 1 or len(symbol_df) >= config.get("max_alarms", 3):
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
        tages_daten = []
        stunden_daten = []
        stunden_strahl_daten = []

        trend_aggregat_roh = erzeuge_trend_aggregat_daten(df)
        zielbalkenLabels = [f"{e['stunde']}h ({e['symbol']})" for e in trend_aggregat_roh]
        zielbalkenDaten = [e["bullish"] - e["bearish"] for e in trend_aggregat_roh]
        zielbalkenFarben = [e["farbe"] if e["farbe"] in ["green", "red"] else "#888" for e in trend_aggregat_roh]
        trend_aggregat_view = {
            "labels": zielbalkenLabels,
            "werte": zielbalkenDaten,
            "farben": zielbalkenFarben
        }
    else:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce', utc=True).dt.tz_convert(MEZ)
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
       

        stunden_daten = erzeuge_stunden_daten(df)
        stunden_strahl_daten = stunden_daten

        trend_aggregat_roh = erzeuge_trend_aggregat_daten(df)
        zielbalkenLabels = [f"{e['stunde']}h ({e['symbol']})" for e in trend_aggregat_roh]
        zielbalkenDaten = [e["bullish"] - e["bearish"] for e in trend_aggregat_roh]
        zielbalkenFarben = [e["farbe"] if e["farbe"] in ["green", "red"] else "#888" for e in trend_aggregat_roh]
        trend_aggregat_view = {
            "labels": zielbalkenLabels,
            "werte": zielbalkenDaten,
            "farben": zielbalkenFarben
        }

    einstellungen = {}
    if os.path.exists(SETTINGS_DATEI):
        with open(SETTINGS_DATEI, "r") as f:
            try:
                einstellungen = json.load(f)
            except Exception as e:
                print("Fehler beim Laden der Einstellungen:", e)
                einstellungen = {}

    return render_template("dashboard.html",
        stunden_daten=stunden_daten,
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
        stunden_daten=stunden_daten,
        stunden_strahl_daten=stunden_strahl_daten,
        trend_aggregat_daten=trend_aggregat_view
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
            except json.JSONDecodeError:
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

