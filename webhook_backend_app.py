import os, json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, redirect, url_for
from dotenv import load_dotenv
import pandas as pd
import smtplib
from email.mime.text import MIMEText
import pytz
from collections import defaultdict
from uploader import git_upload
import threading
from flask_caching import Cache
import time

# ✅ Flask-App & Caching korrekt initialisieren
app = Flask(__name__)
cache = Cache(app, config={'CACHE_TYPE': 'simple'})




global_df = None
df_lock = threading.Lock()

def lade_log_daten():
    global global_df
    try:
        df = pd.read_json(LOG_DATEI, convert_dates=["timestamp"])
        df = df[df["timestamp"].notna()]
        grenze = pd.Timestamp.utcnow() - pd.Timedelta(hours=48)
        df = df[df["timestamp"] > grenze]
        with df_lock:
            global_df = df.copy()
    except Exception as e:
        print(f"[Fehler beim Laden der Logs] {e}")
        global_df = pd.DataFrame(columns=["timestamp", "symbol", "trend"])

lade_log_daten()

def aktualisiere_logs_regelmäßig():
    while True:
        lade_log_daten()
        time.sleep(60)

threading.Thread(target=aktualisiere_logs_regelmäßig, daemon=True).start()

load_dotenv()

LOG_DATEI = "webhook_logs.json"
SETTINGS_DATEI = "settings.json"
# settings.json sicherstellen, falls sie nicht existiert
if not os.path.exists(SETTINGS_DATEI):
    with open(SETTINGS_DATEI, "w") as f:
        json.dump({}, f, indent=2)
EMAIL_ABSENDER = os.getenv("EMAIL_ABSENDER")
EMAIL_PASSWORT = os.getenv("EMAIL_PASSWORT")
EMAIL_EMPFANGER = os.getenv("EMAIL_EMPFANGER")

MEZ = pytz.timezone("Europe/Vienna")
app = Flask(__name__)


def erzeuge_stunden_daten(df: pd.DataFrame, intervall_stunden: int) -> list[dict]:
    df = df[df["trend"].isin(["bullish", "bearish"])].copy()

    # 🔁 Zeitstempel in UTC zu MEZ konvertieren
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce', utc=True).dt.tz_convert("Europe/Vienna")

    # ✅ Optional: Duplikate entfernen, falls mehrfach gesendet wurde
    df = df.drop_duplicates(subset=["timestamp", "symbol", "trend"])

    # 🕓 Korrekte Zuordnung zur Stunden-Zeitgruppe
    df["zeitblock"] = df["timestamp"].dt.floor(f"{intervall_stunden}H")

    # 🔄 Gruppierung nach Symbolart (Dominance/Others)
    df["symbolgruppe"] = df["symbol"].apply(lambda s: "Dominance" if "dominance" in s.lower() else "Others")

    # 📊 Gruppieren nach Zeitblock, Gruppe und Trend
    gruppiert = df.groupby(["zeitblock", "symbolgruppe", "trend"]).size().reset_index(name="anzahl")

    # ➕ Total summieren
    gesamt = gruppiert.groupby(["zeitblock", "trend"])["anzahl"].sum().reset_index()
    gesamt["symbolgruppe"] = "Total"
    gruppiert = pd.concat([gruppiert, gesamt], ignore_index=True)

    # 📦 In strukturierte Dict-Form überführen
    struktur = defaultdict(dict)
    for _, row in gruppiert.iterrows():
        zeit = row["zeitblock"].strftime("%d.%m %H:%M")
        key = f"{row['symbolgruppe']}_{row['trend']}"
        struktur[zeit][key] = row["anzahl"]

    # 📈 Nur die letzten 10 Einträge anzeigen
    daten = []
    for zeit in sorted(struktur.keys())[-10:]:
        eintrag = {"stunde": zeit}
        eintrag.update(struktur[zeit])
        daten.append(eintrag)

    return daten







def erzeuge_trend_aggregat_daten(df: pd.DataFrame) -> list[dict]:
    if "timestamp" not in df.columns or "symbol" not in df.columns:
        return []

    MEZ = pytz.timezone("Europe/Vienna")
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce')

    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")

    df["timestamp"] = df["timestamp"].dt.tz_convert(MEZ)
    df = df[df["timestamp"].notna()]
    df["stunde"] = df["timestamp"].dt.strftime("%H")

    df = df[df["trend"].isin(["bullish", "bearish", "neutral"])]
    gruppiert = df.groupby(["stunde", "symbol", "trend"], observed=False).size().reset_index(name="anzahl")

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



def erzeuge_trend_score_daten(df: pd.DataFrame) -> list[dict]:
    gruppe_1 = ["BTC.D", "ETH.D", "USDT.D", "USDC.D"]
    gruppe_2 = [s for s in df["symbol"].unique() if str(s).startswith("TOTAL/")]

    df = df[df["trend"].isin(["bullish", "bearish"])].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce', utc=True).dt.tz_convert(MEZ)
    df["stunde"] = df["timestamp"].dt.hour

    df["gruppe"] = df["symbol"].apply(lambda x: "Gruppe 1" if x in gruppe_1 else "Gruppe 2" if x in gruppe_2 else None)
    df = df[df["gruppe"].notna()]

    grouped = df.groupby(["stunde", "gruppe", "trend"]).size().unstack(fill_value=0)
    result = []

    for (stunde, gruppe), werte in grouped.groupby(level=[0,1]):
        bullish = werte.get("bullish", 0).values[0] if "bullish" in werte else 0
        bearish = werte.get("bearish", 0).values[0] if "bearish" in werte else 0
        score = bullish - bearish
        farbe = "green" if score > 0 else "red" if score < 0 else "#999"

        result.append({
            "stunde": stunde,
            "gruppe": gruppe,
            "bullish": bullish,
            "bearish": bearish,
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

    git_upload(LOG_DATEI, ".")
    return jsonify({"status": "ok"})


def erzeuge_minichart_daten(df: pd.DataFrame, interval_hours: int = 1) -> dict:
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True).dt.tz_convert(MEZ)
    df = df[df["timestamp"].notna()]
    df = df[df["trend"].isin(["bullish", "bearish", "neutral"])]

    endzeit = datetime.now(MEZ).replace(minute=0, second=0, microsecond=0)
    zeitslots = [(endzeit - timedelta(hours=i * interval_hours)) for i in reversed(range(4))]

    labels = [f"{slot.strftime('%d.%m %H:%M')}" for slot in zeitslots]
    df["slot"] = pd.cut(df["timestamp"], bins=zeitslots + [endzeit + timedelta(hours=interval_hours)],
                        labels=labels, right=False)

    grouped = df.groupby(["symbol", "slot", "trend"]).size().reset_index(name="anzahl")

    minichart_daten = defaultdict(lambda: {"stunden": [], "werte": [], "farben": []})

    for (symbol, slot), gruppe in grouped.groupby(["symbol", "slot"]):
        bullish = gruppe[gruppe["trend"] == "bullish"]["anzahl"].sum()
        bearish = gruppe[gruppe["trend"] == "bearish"]["anzahl"].sum()
        score = bullish - bearish
        farbe = "#00cc66" if score > 0 else "#ff3333" if score < 0 else "#aaaaaa"

        minichart_daten[symbol]["stunden"].append(str(slot))
        minichart_daten[symbol]["werte"].append(score)
        minichart_daten[symbol]["farben"].append(farbe)

    return dict(minichart_daten)



@app.route("/dashboard")
def dashboard():
    year = request.args.get("year")
    mini_interval = request.args.get("mini_interval", "1")
    interval_hours = int(mini_interval) if mini_interval.isdigit() else 1
    stunden_interval = int(request.args.get("stunden_interval", "1"))


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
        zielbalkenLabels = [f"{e['stunde']}h ({e['symbol'][:6]}…)" if len(e['symbol']) > 6 else f"{e['stunde']}h ({e['symbol']})" for e in trend_aggregat_roh]
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

        minicharts = erzeuge_minichart_daten(df, interval_hours=interval_hours)


        # JSON-kompatible Umwandlung für Jinja/Chart.js
        for daten in minicharts.values():
            daten["stunden"] = list(map(str, daten["stunden"]))
            daten["werte"] = [int(w) for w in daten["werte"]]
            daten["farben"] = list(map(str, daten["farben"]))

        df["monat"] = df["timestamp"].dt.strftime("%b")
        df["tag"] = df["timestamp"].dt.date
        df["uhrzeit"] = df["timestamp"].dt.strftime("%H:%M")

        jahre = sorted(df["jahr"].dropna().unique())
        aktuelles_jahr = int(year) if year and year.isdigit() else jahre[-1]
        df_jahr = df[df["jahr"] == aktuelles_jahr]

        monate = [datetime(2025, m, 1).strftime("%b") for m in range(1, 13)]
        matrix = {}


        for symbol in sorted(df_jahr["symbol"].dropna().unique()):
            monatliche_werte = []
            for monat in monate:
                df_monat = df_jahr[(df_jahr["symbol"] == symbol) & (df_jahr["monat"] == monat)]
                bullish = df_monat[df_monat["trend"] == "bullish"].shape[0]
                bearish = df_monat[df_monat["trend"] == "bearish"].shape[0]
                wert = bullish - bearish
                monatliche_werte.append(wert)
            matrix[symbol] = monatliche_werte

        letzte_ereignisse = df.sort_values("timestamp", ascending=False).head(10).to_dict("records")
        fehlerhafte_eintraege = df[df["valid"] != True].sort_values("timestamp", ascending=False).head(10).to_dict("records")

        sortierte_paare = ["BTC", "ETH"] + sorted([s for s in df["symbol"].unique() if s not in ["BTC", "ETH"]])
        df["symbol"] = pd.Categorical(df["symbol"], categories=sortierte_paare, ordered=True)

        stunden_daten = erzeuge_stunden_daten(df, stunden_interval)



        gruppen_trends = []
        farben_mapping = {
            "Dominance_bullish": "lightgreen",
            "Dominance_bearish": "lightcoral",
            "Others_bullish": "#7CFC00",
            "Others_bearish": "#FF4500",
            "Total_bullish": "#00CED1",
            "Total_bearish": "#DC143C"
        }

        for eintrag in stunden_daten:
            stunde = eintrag["stunde"]
            for key, wert in eintrag.items():
                if key == "stunde":
                    continue
                gruppe, richtung = key.split("_")
                gruppen_trends.append({
                    "stunde": stunde,
                    "gruppe": gruppe,
                    "richtung": richtung,
                    "wert": wert,
                    "farbe": farben_mapping.get(key, "#888")
                })


    stunden_strahl_daten = stunden_daten

    trend_aggregat_roh = erzeuge_trend_aggregat_daten(df)
    zielbalkenLabels = [f"{e['stunde']}h ({e['symbol'][:6]}…)" if len(e['symbol']) > 6 else f"{e['stunde']}h ({e['symbol']})" for e in trend_aggregat_roh]
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
        einstellungen=einstellungen,
        letzte_ereignisse=letzte_ereignisse,
        stunden_daten=stunden_daten,
        gruppen_trends=gruppen_trends,
        trend_aggregat_daten=trend_aggregat_view,
        minicharts=minicharts,
        mini_interval=mini_interval,
        stunden_interval=stunden_interval,
        matrix=matrix,
        monate=monate,
        aktuelles_jahr=aktuelles_jahr,
        verfuegbare_jahre=jahre
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
        stunden_interval = int(request.args.get("stunden_interval", "1"))


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

    git_upload(SETTINGS_DATEI, ".")

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
            except Exception as e:
                print("Fehler beim Laden der Einstellungen:", e)
                einstellungen = {}

    for key in keys_to_delete:
        if key in einstellungen:
            del einstellungen[key]

    with open(SETTINGS_DATEI, "w") as f:
        json.dump(einstellungen, f, indent=2)

    git_upload(SETTINGS_DATEI, ".")

    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
