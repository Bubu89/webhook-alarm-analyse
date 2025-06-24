# kurs_handler.py  (komplett)

import json
from datetime import datetime

KURSDATEI = "kursdaten.json"

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

def lade_kurse() -> dict:
    try:
        with open(KURSDATEI, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def speichere_kurse(data: dict) -> None:
    with open(KURSDATEI, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def verarbeite_kursdaten(payload: dict) -> None:
    daten = lade_kurse()
    ts   = payload.get("time", int(datetime.utcnow().timestamp()))
    for fld, pair in SYMBOL_MAP.items():
        price = payload.get(fld)
        if price is None:
            continue
        eintrag = daten.get(fld, {})
        alt     = eintrag.get("alt")
        if alt and ts - alt["timestamp"] >= 7200:
            delta = price - alt["wert"]
            eintrag["verlauf"] = "bullish" if delta > 0 else "bearish" if delta < 0 else "neutral"
            eintrag["trefferquote"] = 1 if (
                (eintrag.get("einschaetzung") == "bullish" and delta > 0) or
                (eintrag.get("einschaetzung") == "bearish" and delta < 0)
            ) else 0
        eintrag["neu"] = {"timestamp": ts, "wert": price}
        eintrag["alt"] = {"timestamp": ts, "wert": price}
        eintrag["symbol"] = pair
        daten[fld] = eintrag
    speichere_kurse(daten)
