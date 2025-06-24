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
import calendar
from kurs_handler import lade_kurse


load_dotenv()  # ganz am Anfang

LOG_DATEI = "webhook_logs.json"
KURSDATEI = "kursdaten.json"

# ✅ Flask-App & Caching korrekt initialisieren - NUR EINMAL
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

SETTINGS_DATEI = "settings.json"
# settings.json sicherstellen, falls sie nicht existiert
if not os.path.exists(SETTINGS_DATEI):
    with open(SETTINGS_DATEI, "w") as f:
        json.dump({}, f, indent=2)
EMAIL_ABSENDER = os.getenv("EMAIL_ABSENDER")
EMAIL_PASSWORT = os.getenv("EMAIL_PASSWORT")
EMAIL_EMPFANGER = os.getenv("EMAIL_EMPFANGER")

MEZ = pytz.timezone("Europe/Vienna")


def erzeuge_stunden_daten(df: pd.DataFrame, intervall_stunden: int) -> list[dict]:
    # ... Funktion bleibt unverändert
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

    # 🆕 Total separat aus allen Gruppen berechnen
    gesamt = gruppiert.groupby(["zeitblock", "trend"])["anzahl"].sum().reset_index()
    gesamt["symbolgruppe"] = "Total"

    # Reihenfolge beibehalten
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
            return json.load(f)
    except Exception:
        return []

def extrahiere_letzte_signale(logs):
    if not logs:
        return []
    sortierte = sorted(logs, key=lambda x: x.get("timestamp", ""), reverse=True)
    return sortierte[:10]

def extrahiere_letzte_scores(prognosen):
    if not prognosen:
        return {}
    # Beispiel: einfach die Scores aus dem Prognosen-Dict holen
    return {k: v.get("score", 0) for k, v in prognosen.items()}

def trend_verlauf_letzte_stunden(logs, stunden=6):
    if not logs:
        return []
    jetzt = datetime.now(MEZ)
    grenze = jetzt - timedelta(hours=stunden)
    gefiltert = [e for e in logs if "timestamp" in e and datetime.fromisoformat(e["timestamp"]) >= grenze]
    # Gruppieren nach Symbol und Trend für letzten Zeitraum
    ergebnis = defaultdict(lambda: {"bullish": 0, "bearish": 0})
    for eintrag in gefiltert:
        symbol = eintrag.get("symbol")
        trend = eintrag.get("trend")
        if symbol and trend in ["bullish", "bearish"]:
            ergebnis[symbol][trend] += 1
    return ergebnis

@app.route("/dashboard")
def dashboard():
    try:
        # URL-Parameter auslesen
        year = request.args.get("year")
        mini_interval = request.args.get("mini_interval", "1")
        interval_hours = int(mini_interval) if mini_interval.isdigit() else 1
        stunden_interval = int(request.args.get("stunden_interval", "1"))

        # Logs laden
        logs = lade_logs()
        if isinstance(logs, dict):
            logs = [logs]
        logs = [e for e in logs if isinstance(e, dict)]

        # DataFrame aus Logs
        df = pd.DataFrame(logs)
        if df.empty:
            # Fallback-Werte wenn keine Daten vorhanden
            jahre = [datetime.now().year]
            aktuelles_jahr = int(year) if year and year.isdigit() else jahre[0]
            monate = [datetime(aktuelles_jahr, m, 1).strftime("%b") for m in range(1, 13)]
            matrix = {}
            letzte_ereignisse = []
            stunden_daten = []
            minicharts = {}
            gruppen_trends = []
            trend_aggregat_view = {"labels": [], "werte": [], "farben": []}
            prognosen = {}
        else:
            # Timestamp parsen und Zeitzone setzen
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce', utc=True).dt.tz_convert(MEZ)
            df["symbol"] = df["symbol"].astype(str)
            df["jahr"] = df["timestamp"].dt.year
            aktuelles_jahr = int(year) if year and year.isdigit() else df["jahr"].max()
            df_jahr = df[df["jahr"] == aktuelles_jahr]
            monate = [datetime(aktuelles_jahr, m, 1).strftime("%b") for m in range(1, 13)]

            # Prognosen laden oder berechnen
            prognosen = berechne_prognosen(df)

            # Matrix für monatliche Verteilung
            matrix = {}
            for symbol in sorted(df_jahr["symbol"].unique()):
                monatliche_werte = []
                for monat in monate:
                    df_monat = df_jahr[(df_jahr["symbol"] == symbol) & (df_jahr["timestamp"].dt.strftime("%b") == monat)]
                    bullish = df_monat[df_monat["trend"] == "bullish"].shape[0]
                    bearish = df_monat[df_monat["trend"] == "bearish"].shape[0]
                    monatliche_werte.append(bullish - bearish)
                matrix[symbol] = monatliche_werte

            # Letzte 10 Alarme (sortiert nach timestamp absteigend)
            letzte_ereignisse = df.sort_values("timestamp", ascending=False).head(10).to_dict("records")

            # Stunden-Daten (für Stunden-Chart)
            stunden_daten = erzeuge_stunden_daten(df, stunden_interval)

            # Gruppen-Trends für Farbdarstellung aufbereiten
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

            # Mini-Charts Daten
            minicharts = erzeuge_minichart_daten(df, interval_hours=interval_hours)
            # Daten für Chart.js (string, int, string)
            for daten in minicharts.values():
                daten["stunden"] = list(map(str, daten["stunden"]))
                daten["werte"] = [int(w) for w in daten["werte"]]
                daten["farben"] = list(map(str, daten["farben"]))

            # Trend Aggregat Daten (für Balkendiagramme)
            trend_aggregat_roh = erzeuge_trend_aggregat_daten(df)
            labels = [f"{e['stunde']}h ({e['symbol'][:6]}…)" if len(e['symbol']) > 6 else f"{e['stunde']}h ({e['symbol']})" for e in trend_aggregat_roh]
            werte = [e["bullish"] - e["bearish"] for e in trend_aggregat_roh]
            farben = [e["farbe"] if e["farbe"] in ["green", "red"] else "#888" for e in trend_aggregat_roh]
            trend_aggregat_view = {
                "labels": labels,
                "werte": werte,
                "farben": farben
            }

            jahre = sorted(df["jahr"].unique())

        # Einstellungen laden
        einstellungen = {}
        if os.path.exists(SETTINGS_DATEI):
            try:
                with open(SETTINGS_DATEI, "r") as f:
                    einstellungen = json.load(f)
            except Exception as e:
                print("Fehler beim Laden der Einstellungen:", e)

        # Template rendern mit allen nötigen Variablen
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
                               verfuegbare_jahre=jahre,
                               prognosen=prognosen)

    except Exception as e:
        return f"Fehler im Dashboard: {e}"



# ...

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
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
@app.route("/kursdaten", methods=["POST"])
def empfange_kursdaten():
    daten = request.get_json()
    symbol = daten.get("symbol", "").upper()
    trend = daten.get("trend", "neutral")
    nachricht = daten.get("nachricht", "")
    kurswert = float(daten.get("wert", 0))

    # Speichere Alarmsignal
    empfange_webhook(symbol, trend, nachricht)

    # Speichere Kurswert separat
    if symbol and kurswert > 0:
        eintrag = {
            "symbol": symbol,
            "wert": kurswert,
            "zeit": datetime.utcnow().isoformat()
        }
        with open("kurswerte.json", "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag) + "\n")

    return jsonify({"status": "ok"})


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


def berechne_prognosen(df: pd.DataFrame) -> dict:
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True).dt.tz_convert(MEZ)

    def trend_score(symbol):
        symbol_df = df[df["symbol"] == symbol]
        bullish = symbol_df[symbol_df["trend"] == "bullish"].shape[0]
        bearish = symbol_df[symbol_df["trend"] == "bearish"].shape[0]
        return bullish - bearish

    def total_coti_ratio():
        total = trend_score("TOTAL")
        coti = trend_score("COTIUSD")
        if coti == 0: return 0
        return total / coti

    prognosen = {
        "COTI": {
            "score": trend_score("COTIUSD"),
            "relation": round(total_coti_ratio(), 2),
            "signal": "🟢" if total_coti_ratio() < 1 else "🔴"
        },
        "ETH": {
            "score": trend_score("ETHUSD"),
            "signal": "🟢" if trend_score("ETHUSD") > 0 else "🔴"
        },
        "VELO": {
            "score": trend_score("VELOUSD"),
            "signal": "🟢" if trend_score("VELOUSD") > 0 else "🔴"
        },
        "Altcoins": {
            "score": trend_score("TOTAL") + trend_score("Others"),
            "signal": "🟢" if (trend_score("TOTAL") + trend_score("Others")) > 0 else "🔴"
        },
        "BTC": {
            "score": trend_score("BTCUSD"),
            "signal": "🟢" if trend_score("BTCUSD") > 0 else "🔴"
        }
    }

    # 🆕 Erweiterung: High-Signale für COTI einbinden
    anzahl_high_signale = df[df["symbol"] == "COTIUSD"].shape[0]
    prognosen["COTI"]["highs"] = anzahl_high_signale
    if total_coti_ratio() < 1 and anzahl_high_signale >= 3:
        prognosen["COTI"]["signal"] = "🟢"

    return prognosen


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

