import os, json
from datetime import datetime, timedelta

from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for
)
from dotenv import load_dotenv
import pandas as pd
import smtplib
from email.mime.text import MIMEText
import pytz

from collections import defaultdict, OrderedDict   # ← OrderedDict NEU
from uploader import git_upload
import threading
from flask_caching import Cache
import time

from kurs_handler import lade_kurse, verarbeite_kursdaten  # Kurs-Handling
from utils_minichart import erzeuge_minichart_daten, MEZ


# ─────────────────────────  pandas-Warnung ausblenden  ─────────────────────────
import warnings
warnings.filterwarnings(
    "ignore",
    message="The default of observed=False is deprecated",
    category=FutureWarning,
    module="pandas.core.groupby",
)

#......................................................
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
#......................................................

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

#...................................................... Neu
def erzeuge_monats_matrix(df: pd.DataFrame, jahr: int) -> pd.DataFrame:
    df = df[df["timestamp"].dt.year == jahr].copy()
    df["monat"] = df["timestamp"].dt.month

    matrix = df.groupby(["symbol", "monat", "trend"]).size().unstack(fill_value=0)

    if "bullish" not in matrix.columns:
        matrix["bullish"] = 0
    if "bearish" not in matrix.columns:
        matrix["bearish"] = 0

    matrix["gesamt"] = matrix["bullish"] - matrix["bearish"]
    matrix = matrix.reset_index()

    # Monatswerte extrahieren für separate Spalten
    monatliche_trends = []
    for monat in range(1, 13):
        df_monat = df[df["monat"] == monat]
        trends = df_monat.groupby(["symbol", "trend"]).size().unstack(fill_value=0)
        trends["gesamt"] = trends.get("bullish", 0) - trends.get("bearish", 0)
        trends = trends.reset_index()
        trends = trends[["symbol", "gesamt"]].rename(columns={"gesamt": calendar.month_abbr[monat]})
        monatliche_trends.append(trends)

    # Initialisiere Gesamtdaten mit allen Symbolen
    alle_symbole = pd.concat(monatliche_trends)["symbol"].unique()
    matrix_final = pd.DataFrame({"Symbol": alle_symbole})

    # Füge Monatsspalten hinzu
    for trends in monatliche_trends:
        matrix_final = matrix_final.merge(trends, on="symbol", how="left")

    matrix_final = matrix_final.fillna(0)

    # Sortierung gemäß Prognose-Dashboard
    symbol_sortierung = [
        "BTCUSD", "BTC.D", "BTC.D+CRYPTOCAP:ETH.D+CRYPTOCAP:USDT.D+CRYPTOCAP:USDC.D",
        "ETHUSD", "COTIUSDT", "VELOUSDT", "BONKUSDT",
        "TOTAL/COINBASE:COTIUSD", "TOTAL/COINBASE:OPUSD",
        "TOTAL/BINANCE:FETUSD", "OTHERS/INDEX:BTCUSD"
    ]

    matrix_final["Symbol"] = pd.Categorical(matrix_final["Symbol"], categories=symbol_sortierung, ordered=True)
    matrix_final = matrix_final.sort_values("Symbol").reset_index(drop=True)

    return matrix_final
#...................................................... Neu
def erzeuge_stunden_daten(df: pd.DataFrame, intervall_stunden: int) -> list[dict]:
    # ... Funktion bleibt unverändert
    df = df[df["trend"].isin(["bullish", "bearish"])].copy()

    # 🔁 Zeitstempel in UTC zu MEZ konvertieren
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce', utc=True).dt.tz_convert("Europe/Vienna")

    # ✅ Optional: Duplikate entfernen, falls mehrfach gesendet wurde
    df = df.drop_duplicates(subset=["timestamp", "symbol", "trend"])

    # 🕓 Korrekte Zuordnung zur Stunden-Zeitgruppe
    df["zeitblock"] = df["timestamp"].dt.floor(f"{intervall_stunden}h")

    # 🔄 Gruppierung nach Symbolart (Dominance/Others)
    DOMI = {"BTC.D", "ETH.D", "USDT.D", "USDC.D"}          # kannst Du beliebig erweitern
    df["symbolgruppe"] = df["symbol"].apply(
        lambda s: "Dominance" if s.upper() in DOMI else "Others"
    )


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
            daten = json.load(f)
            if isinstance(daten, list):
                return daten[-100:]  # ⏩ nur die letzten 100 Einträge
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

# ------------------------------------------------------------------
#  ➜  NEU: mehrere globale Settings per Checkbox löschen
# ------------------------------------------------------------------
@app.route("/delete-multiple-settings", methods=["POST"])
def delete_multiple_settings():
    keys = request.form.getlist("delete_keys")
    if not keys:                       # nichts markiert
        return redirect(url_for("dashboard"))

    # Settings-Datei laden
    try:
        with open(SETTINGS_DATEI, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except Exception:
        settings = {}

    # markierte Keys entfernen
    for k in keys:
        settings.pop(k, None)

    # zurückschreiben
    with open(SETTINGS_DATEI, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)

    # (optional) Auto-Commit
    git_upload(SETTINGS_DATEI, ".")

    return redirect(url_for("dashboard"))
# ------------------------------------------------------------------
#  ➜  NEU: globale Alarmeinstellungen speichern / überschreiben
# ------------------------------------------------------------------
@app.route("/update-settings", methods=["POST"])
def update_settings():
    # Werte aus dem Formular holen
    interval_dropdown = request.form.get("interval_hours_dropdown", type=int)
    interval_manual   = request.form.get("interval_hours_manual", type=int)
    max_alarms        = request.form.get("max_alarms", type=int, default=3)
    trend             = request.form.get("trend_richtung", default="neutral")
    symbol            = request.form.get("symbol", default="ALL")

    # manuelle Eingabe hat Vorrang
    interval_hours = interval_manual or interval_dropdown or 1

    # Settings-Datei einlesen
    try:
        with open(SETTINGS_DATEI, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except Exception:
        settings = {}

    key = f"global_{trend}" if symbol == "ALL" else f"global_{trend}_{symbol}"

    settings[key] = {
        "interval_hours": interval_hours,
        "max_alarms":     max_alarms,
        "trend_richtung": trend
    }

    # speichern
    with open(SETTINGS_DATEI, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)

    git_upload(SETTINGS_DATEI, ".")          # optionaler Auto-Commit
    return redirect(url_for("dashboard"))

@app.route("/kursdaten", methods=["POST"])
def empfange_kursdaten():
    payload_raw = request.get_data(as_text=True)      # egal welcher Content-Type
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError as err:
        return jsonify({"error": f"Ungültiges JSON: {err}"}), 400

    verarbeite_kursdaten(payload)                     # schreibt/aktualisiert kursdaten.json
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

# ............................................................

def berechne_prognosen(df: pd.DataFrame) -> dict:
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True).dt.tz_convert(MEZ)
    kurse = lade_kurse()                       # <- neue Kursbasis

    def trend_score(sym):
        s = df[df["symbol"] == sym]
        return len(s[s["trend"] == "bullish"]) - len(s[s["trend"] == "bearish"])

    def kurs_info(key):                        # Helper für Preis / Trefferquote
        k = kurse.get(key, {})
        return k.get("neu", {}).get("wert", 0), k.get("trefferquote", 0)

    prognosen = {}
    for asset, key in {"COTI":"coti","ETH":"eth","VELO":"velo",
                       "BTC":"btc","Altcoins":"total"}.items():
        preis, treffer = kurs_info(key)
        score = trend_score(f"{asset}USD") if asset not in ("Altcoins",) else \
                trend_score("TOTAL") + trend_score("Others")

        prognosen[asset] = {
            "score": score,
            "kurs": preis,
            "trefferquote": treffer,
            "signal": "🟢" if score > 0 else "🔴"
        }

    # COTI-Sonderfall …
        prognosen = dict(sorted(prognosen.items(), key=lambda item: sortschlüssel_prognose(item[0])))

    return prognosen
