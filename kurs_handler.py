import json
from datetime import datetime, timedelta

KURSDATEI = "kursdaten.json"

def lade_kurse():
    try:
        with open(KURSDATEI, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def speichere_kurse(daten):
    with open(KURSDATEI, "w", encoding="utf-8") as f:
        json.dump(daten, f, indent=2)

def verarbeite_kursdaten(payload):
    daten = lade_kurse()
    zeitstempel = payload.get("time")

    symbole = ["btc", "eth", "coti", "velo", "op", "fet", "aero", "tao"]
    for symbol in symbole:
        kurs_neu = payload.get(symbol)
        if kurs_neu is None:
            continue

        eintrag = daten.get(symbol, {})
        eintrag["neu"] = {"timestamp": zeitstempel, "wert": kurs_neu}

        # Letzter bekannter Kurs
        alt = eintrag.get("alt")
        if alt and abs(zeitstempel - alt["timestamp"]) >= 7200:
            delta = kurs_neu - alt["wert"]
            eintrag["verlauf"] = "bullish" if delta > 0 else "bearish" if delta < 0 else "neutral"
            # Trefferquote hier (Dummy)
            eintrag["trefferquote"] = berechne_trefferquote(delta, eintrag.get("einschaetzung"))

        eintrag["alt"] = {"timestamp": zeitstempel, "wert": kurs_neu}
        daten[symbol] = eintrag

    speichere_kurse(daten)

def berechne_trefferquote(delta, einschätzung):
    if not einschätzung:
        return 0
    if einschätzung == "bullish" and delta > 0:
        return 1
    if einschätzung == "bearish" and delta < 0:
        return 1
    return 0
