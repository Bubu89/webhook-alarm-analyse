# kurs_handler.py  (komplett)

import json, os, tempfile, shutil
from datetime import datetime

# --------------------------------------------------------------------
# Pfad & Konstanten
# --------------------------------------------------------------------
KURSDATEI = "kursdaten.json"

# Mindestspeicherdauer für eine „alt“-Messung (3 h = 10 800 s)
MIN_TTL = 1 * 3600   # 1 h

# Zuordnung aus Webhook-Payload → Symbol-String
SYMBOL_MAP = {
    "btc":  "BTCUSD",
    "eth":  "ETHUSD",
    "coti": "COTIUSD",
    "velo": "VELOUSD",
    "op":   "OPUSD",
    "fet":  "FETUSD",
    "aero": "AEROUSD",
    "tao":  "TAOUSD"
}

# --------------------------------------------------------------------
# Helfer
# --------------------------------------------------------------------
def lade_kurse() -> dict:
    try:
        with open(KURSDATEI, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def speichere_kurse(data: dict) -> None:
    """
    Kurs-JSON atomar schreiben (erst in temp-Datei, dann rename).
    Verhindert, dass parallele Schreibzugriffe die Datei korrumpieren.
    """
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(KURSDATEI) or ".")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        shutil.move(tmp_path, KURSDATEI)   # atomic rename
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

# --------------------------------------------------------------------
# Haupt-Funktion: eingehende Preise verarbeiten
# --------------------------------------------------------------------
def verarbeite_kursdaten(payload: dict) -> None:
    daten = lade_kurse()
    ts    = payload.get("time", int(datetime.utcnow().timestamp()))

    for fld, pair in SYMBOL_MAP.items():
        raw_price = payload.get(fld)
        if raw_price is None:
            continue

        # Preis-Wert als float casten, damit Rechenoperationen sicher funktionieren
        price   = float(raw_price)
        eintrag = daten.get(fld, {})
        alt     = eintrag.get("alt")

        # ----------------------------------------------------------
        # „alt“ nur überschreiben, wenn die letzte Messung ≥ MIN_TTL zurückliegt
        # ----------------------------------------------------------
        if alt and ts - alt["timestamp"] >= MIN_TTL:
            delta = price - alt["wert"]
            eintrag["verlauf"] = (
                "bullish" if delta > 0 else
                "bearish" if delta < 0 else
                "neutral"
            )
            eintrag["trefferquote"] = 1 if (
                (eintrag.get("einschaetzung") == "bullish" and delta > 0) or
                (eintrag.get("einschaetzung") == "bearish" and delta < 0)
            ) else 0
            # alt aktualisieren
            eintrag["alt"] = {"timestamp": ts, "wert": price}

        # immer den neuesten Preis merken
        eintrag["neu"]    = {"timestamp": ts, "wert": price}
        eintrag["symbol"] = pair
        daten[fld]        = eintrag

    speichere_kurse(daten)
