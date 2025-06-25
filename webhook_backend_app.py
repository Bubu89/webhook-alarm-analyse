import os
import json
import time
import threading
import warnings
import smtplib
import pytz
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict, OrderedDict
from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_caching import Cache
from dotenv import load_dotenv
from email.mime.text import MIMEText

from uploader import git_upload
from kurs_handler import lade_kurse, verarbeite_kursdaten
from utils_minichart import erzeuge_minichart_daten, MEZ

# Warnungen von pandas unterdrücken (Gruppierungswarnung)
warnings.filterwarnings(
    "ignore",
    message="The default of observed=False is deprecated",
    category=FutureWarning,
    module="pandas.core.groupby",
)

# Initialisiere Flask-App und Cache
load_dotenv()
app = Flask(__name__)
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

LOG_DATEI = "webhook_logs.json"
KURSDATEI = "kursdaten.json"
SETTINGS_DATEI = "settings.json"

EMAIL_ABSENDER = os.getenv("EMAIL_ABSENDER")
EMAIL_PASSWORT = os.getenv("EMAIL_PASSWORT")
EMAIL_EMPFANGER = os.getenv("EMAIL_EMPFANGER")

MEZ = pytz.timezone("Europe/Vienna")

global_df = None
df_lock = threading.Lock()

@app.route("/dashboard")
def dashboard():
    try:
        logs = lade_logs()
        df = pd.DataFrame(logs)
        prognosen = berechne_prognosen(df)
        letzte_ereignisse = df.sort_values("timestamp", ascending=False).head(10).to_dict("records") if not df.empty else []
        minicharts = erzeuge_minichart_daten(df, interval_hours=1) if not df.empty else {}
        stunden_daten = erzeuge_trend_aggregat_daten(df)
# oder deine eigene erzeuge_stunden_daten(df, 1)
        einstellungen = {}
        if os.path.exists(SETTINGS_DATEI):
            with open(SETTINGS_DATEI, "r") as f:
                einstellungen = json.load(f)

        return render_template("dashboard.html",
                               prognosen=prognosen,
                               letzte_ereignisse=letzte_ereignisse,
                               stunden_daten=stunden_daten,
                               minicharts=minicharts,
                               mini_interval="1",
                               stunden_interval=1,
                               matrix={},
                               monate=["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"],
                               aktuelles_jahr=datetime.now().year,
                               verfuegbare_jahre=[datetime.now().year],
                               einstellungen=einstellungen)
    except Exception as e:
        return f"Fehler im Dashboard: {e}"


def normiere_symbol_prognose(s: str) -> str:
    s = s.upper()
    s = s.replace("COINBASE:", "")
    s = s.replace("BINANCE:", "")
    s = s.replace("INDEX:", "")
    s = s.replace("CRYPTOCAP:", "")
    s = s.replace("TOTAL/", "")
    s = s.replace("USDT", "USD")
    return s.split("+")[0]

prior_liste = [
    "BTC", "ETH", "ALTCOINS", "OTHERS", "TOTAL"
]

def sortschlüssel_prognose(sym: str):
    kern = normiere_symbol_prognose(sym)
    for i, prior in enumerate(prior_liste):
        if kern.startswith(prior):
            return (0, i)
    if "TOTAL/" in sym.upper():
        base = normiere_symbol_prognose(sym.split("/", 1)[-1])
        return (1, base)
    return (2, kern)

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

# settings.json sicherstellen, falls nicht vorhanden
if not os.path.exists(SETTINGS_DATEI):
    with open(SETTINGS_DATEI, "w") as f:
        json.dump({}, f, indent=2)

def extrahiere_hauptsymbol(symbol):
    if "/" in symbol:
        symbol = symbol.split("/")[-1]
    if ":" in symbol:
        symbol = symbol.split(":")[-1]
    if symbol.endswith("USDT"):
        symbol = symbol[:-4]
    elif symbol.endswith("USD"):
        symbol = symbol[:-3]
    return symbol.upper()

priorisierte_reihenfolge = [
    "BTC.D", "ETH.D", "OTHERS", "BTC", "ETH",
    "AERO", "COTI", "FET", "OP", "TAO", "VELO"
]

def sortierschluessel(symbol):
    hs = extrahiere_hauptsymbol(symbol)
    if "BTC.D+" in symbol:
        return (0, hs)
    if "BTC.D" in symbol:
        return (1, "")
    if "ETH.D" in symbol:
        return (2, "")
    if "OTHERS" in symbol:
        return (3, "")
    if hs in priorisierte_reihenfolge:
        return (4, priorisierte_reihenfolge.index(hs))
    return (5, hs)

def lade_prognosen():
    PROGNOSE_DATEI = "prognosen.json"
    try:
        with open(PROGNOSE_DATEI, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def lade_logs():
    try:
        with open(LOG_DATEI, "r", encoding="utf-8") as f:
            daten = json.load(f)
            if isinstance(daten, list):
                return daten[-100:]
            return [daten] if isinstance(daten, dict) else []
    except Exception as e:
        print(f"[lade_logs] Fehler beim Einlesen: {e}")
        return []

def extrahiere_letzte_signale(logs):
    if not logs:
        return []
    sortierte = sorted(logs, key=lambda x: x.get("timestamp", ""), reverse=True)
    return sortierte[:10]

def extrahiere_letzte_scores(prognosen):
    if not prognosen:
        return {}
    return {k: v.get("score", 0) for k, v in prognosen.items()}

def trend_verlauf_letzte_stunden(logs, stunden=6):
    if not logs:
        return []
    jetzt = datetime.now(MEZ)
    grenze = jetzt - timedelta(hours=stunden)
    gefiltert = [
        e for e in logs
        if "timestamp" in e and datetime.fromisoformat(e["timestamp"]) >= grenze
    ]
    ergebnis = defaultdict(lambda: {"bullish": 0, "bearish": 0})
    for eintrag in gefiltert:
        symbol = eintrag.get("symbol")
        trend = eintrag.get("trend")
        if symbol and trend in ["bullish", "bearish"]:
            ergebnis[symbol][trend] += 1
    return ergebnis

@app.route("/")
def home():
    return redirect("/dashboard")

@app.route("/kursdaten", methods=["POST"])
def empfange_kursdaten():
    payload_raw = request.get_data(as_text=True)
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError as err:
        return jsonify({"error": f"Ungültiges JSON: {err}"}), 400

    verarbeite_kursdaten(payload)
    return jsonify({"status": "ok"})

@app.route("/delete-multiple-settings", methods=["POST"])
def delete_multiple_settings():
    keys = request.form.getlist("delete_keys")
    if not keys:
        return redirect(url_for("dashboard"))

    try:
        with open(SETTINGS_DATEI, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except Exception:
        settings = {}

    for k in keys:
        settings.pop(k, None)

    with open(SETTINGS_DATEI, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)

    git_upload(SETTINGS_DATEI, ".")
    return redirect(url_for("dashboard"))

@app.route("/update-settings", methods=["POST"])
def update_settings():
    interval_dropdown = request.form.get("interval_hours_dropdown", type=int)
    interval_manual = request.form.get("interval_hours_manual", type=int)
    max_alarms = request.form.get("max_alarms", type=int, default=3)
    trend = request.form.get("trend_richtung", default="neutral")
    symbol = request.form.get("symbol", default="ALL")

    interval_hours = interval_manual or interval_dropdown or 1

    try:
        with open(SETTINGS_DATEI, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except Exception:
        settings = {}

    key = f"global_{trend}" if symbol == "ALL" else f"global_{trend}_{symbol}"
    settings[key] = {
        "interval_hours": interval_hours,
        "max_alarms": max_alarms,
        "trend_richtung": trend
    }

    with open(SETTINGS_DATEI, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)

    git_upload(SETTINGS_DATEI, ".")
    return redirect(url_for("dashboard"))

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
    symbol_df = symbol_df[symbol_df["timestamp"] < data["timestamp"]]

    if config.get("max_alarms", 3) == 1 or len(symbol_df) >= config.get("max_alarms", 3):
        sende_email(f"Alarm: {symbol}", f"{len(symbol_df)} Alarme in {config['interval_hours']}h ({trend})")

    git_upload(LOG_DATEI, ".")
    return jsonify({"status": "ok"})

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

def berechne_prognosen(df: pd.DataFrame) -> dict:
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True).dt.tz_convert(MEZ)
    kurse = lade_kurse()

    def trend_score(sym):
        s = df[df["symbol"] == sym]
        return len(s[s["trend"] == "bullish"]) - len(s[s["trend"] == "bearish"])

    def kurs_info(key):
        k = kurse.get(key, {})
        return k.get("neu", {}).get("wert", 0), k.get("trefferquote", 0)

    prognosen = {}
    for asset, key in {
        "COTI": "coti",
        "ETH": "eth",
        "VELO": "velo",
        "BTC": "btc",
        "Altcoins": "total"
    }.items():
        preis, treffer = kurs_info(key)
        score = trend_score(f"{asset}USD") if asset != "Altcoins" else \
            trend_score("TOTAL") + trend_score("Others")

        prognosen[asset] = {
            "score": score,
            "kurs": preis,
            "trefferquote": treffer,
            "signal": "🟢" if score > 0 else "🔴"
        }

    prognosen = dict(sorted(prognosen.items(), key=lambda item: sortschlüssel_prognose(item[0])))
    return prognosen

def erzeuge_trend_aggregat_daten(df: pd.DataFrame) -> list[dict]:
    if "timestamp" not in df.columns or "symbol" not in df.columns:
        return []

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

    df["gruppe"] = df["symbol"].apply(
        lambda x: "Gruppe 1" if x in gruppe_1 else "Gruppe 2" if x in gruppe_2 else None
    )
    df = df[df["gruppe"].notna()]

    grouped = df.groupby(["stunde", "gruppe", "trend"]).size().unstack(fill_value=0)
    result = []

    for (stunde, gruppe), werte in grouped.groupby(level=[0, 1]):
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
