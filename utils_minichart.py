from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta
import pandas as pd
import pytz

MEZ = pytz.timezone("Europe/Vienna")

def normiere_symbol(s: str) -> str:
    s = s.upper()
    s = s.replace("COINBASE:", "")
    s = s.replace("BINANCE:", "")
    s = s.replace("INDEX:", "")
    s = s.replace("CRYPTOCAP:", "")
    s = s.replace("TOTAL/", "")
    s = s.replace("USDT", "USD")
    return s.split("+")[0]

def erzeuge_minichart_daten(df: pd.DataFrame, interval_hours: int = 1) -> dict:
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True).dt.tz_convert(MEZ)
    df = df[df["timestamp"].notna() & df["trend"].isin(["bullish", "bearish", "neutral"])]

    endzeit = datetime.now(MEZ).replace(minute=0, second=0, microsecond=0)
    zeitslots = [(endzeit - timedelta(hours=i * interval_hours)) for i in reversed(range(4))]
    labels = [slot.strftime("%d.%m %H:%M") for slot in zeitslots]

    df["slot"] = pd.cut(
        df["timestamp"],
        bins=zeitslots + [endzeit + timedelta(hours=interval_hours)],
        labels=labels,
        right=False,
    )

    grouped = df.groupby(["symbol", "slot", "trend"]).size().reset_index(name="anzahl")

    raw = defaultdict(lambda: {"stunden": [], "werte": [], "farben": []})
    for (symbol, slot), g in grouped.groupby(["symbol", "slot"]):
        bullish = g.loc[g["trend"] == "bullish", "anzahl"].sum()
        bearish = g.loc[g["trend"] == "bearish", "anzahl"].sum()
        score = int(bullish - bearish)
        farbe = "#00cc66" if score > 0 else "#ff3333" if score < 0 else "#aaaaaa"

        raw[symbol]["stunden"].append(str(slot))
        raw[symbol]["werte"].append(score)
        raw[symbol]["farben"].append(farbe)

    dominanz_set = {
        "BTC.D", "ETH.D", "USDT.D", "USDC.D", "TOTAL", "TOTAL2", "TOTAL3", "OTHERS", "CRYPTOCAP", "DEFI"
    }

    def sortschlüssel(sym):
        kern = normiere_symbol(sym)

        if kern == "BTC.D":
            return (0, "A")
        if kern == "ETH.D":
            return (0, "B")
        if kern in dominanz_set:
            return (0, kern)

        if kern == "BTCUSD":
            return (1, "A")
        if kern == "ETHUSD":
            return (1, "B")

        if any(stable in kern for stable in ["USDT", "USDC", "DAI", "TUSD", "USD"]):
            return (2, kern)

        if kern.startswith("TOTAL/"):
            base = kern.split("/", 1)[1]
            return (4, base + "_total")

        return (3, kern)

    hauptsymbole = sorted(raw.keys(), key=sortschlüssel)
    geordnet = []
    hinzugefügt = set()

    for sym in hauptsymbole:
        if sym in hinzugefügt:
            continue
        geordnet.append(sym)
        hinzugefügt.add(sym)

        normiert = normiere_symbol(sym)
        total_sym = f"TOTAL/{sym}"
        alt1 = f"TOTAL/{normiert}"
        alt2 = f"TOTAL/COINBASE:{normiert}"
        alt3 = f"TOTAL/BINANCE:{normiert}"

        for variant in [total_sym, alt1, alt2, alt3]:
            if variant in raw and variant not in hinzugefügt:
                geordnet.append(variant)
                hinzugefügt.add(variant)

    for sym in raw:
        if sym not in hinzugefügt:
            geordnet.append(sym)

    return OrderedDict((k, raw[k]) for k in geordnet)
