# kurs_handler.py
# ------------------------------------------------------------------
#  Speichert eingehende Kurs-Webhooks (5-min-Takt) als JSON-Historie
#  und berechnet einfache Verlauf–/Treffer-Informationen.
# ------------------------------------------------------------------

import json
from datetime import datetime

KURSDATEI = "kursdaten.json"

# Feldname im Payload  ➜  Symbol-Kennung im Dashboard / Logs
SYMBOL_MAP = {
    "btc":   "BTCUSD",
    "eth":   "ETHUSD",
    "coti":  "COTIUSD",
    "velo":  "VELOUSD",
    "op":    "OPUSD",
    "fet":   "FETUSD",
    "aero":  "AEROUSD",
    "tao":   "TAOUSD",
    "total": "TOTAL"       #  <<<  Altcoin-Gesamtmarkt (Market-Cap)
}


# ------------------------------------------------------------------
#  Helfer: Datei-Handling
# ------------------------------------------------------------------
def lade_kurse() -> dict:
    """Liest kursdaten.json oder liefert leeres Dict."""
    try:
        with open(KURSDATEI, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as err:
        print("[kurs_handler] Fehler beim Laden:", err)
        return {}


def speichere_kurse(data: dict) -> None:
    """Schreibt das Dict sauber formatiert zurück."""
    with open(KURSDATEI, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ------------------------------------------------------------------
#  Kern-Routine: eingehendes Payload verarbeiten
# ------------------------------------------------------------------
def verarbeite_kursdaten(payload: dict) -> None:
    """
    Erwartet das 5-Min-Webhook-Payload von TradingView.
    Aktualisiert für jedes Feld in SYMBOL_MAP:
      • neu  (aktueller Preis + Zeitstempel)
      • alt  (letzter Preis vor ≥2 h)
      • verlauf  (bullish / bearish / neutral)
      • trefferquote (0/1 Dummy – kann später verfeinert werden)
    """
    daten   = lade_kurse()
    ts      = int(payload.get("time", datetime.utcnow().timestamp()))

    for fld, pair in SYMBOL_MAP.items():
        price = payload.get(fld)
        if price is None:          # Feld fehlt im JSON
            continue

        eintrag = daten.get(fld, {})
        alt     = eintrag.get("alt")

        # ------------------------------------------- Kurs-Differenz ≥ 2 h
        if alt and ts - alt["timestamp"] >= 7200:
            delta  = price - alt["wert"]
            verl   = "bullish" if delta > 0 else "bearish" if delta < 0 else "neutral"
            quote  = (
                1 if (
                    (eintrag.get("einschaetzung") == "bullish" and delta > 0) or
                    (eintrag.get("einschaetzung") == "bearish" and delta < 0)
                ) else 0
            )
            eintrag["verlauf"]       = verl
            eintrag["trefferquote"]  = quote

        # ------------------------------------------- aktuelle Werte setzen
        eintrag["neu"]    = {"timestamp": ts, "wert": price}
        eintrag["alt"]    = {"timestamp": ts, "wert": price}   # wird nach 2 h ersetzt
        eintrag["symbol"] = pair

        daten[fld] = eintrag

    speichere_kurse(daten)
