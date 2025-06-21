import os, json
import random
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
import pandas as pd

LOG_DATEI = "webhook_logs.json"
app = Flask(__name__)

@app.route("/")
def index():
    return "OK - Service laeuft"

@app.route("/webhook", methods=["POST"])
def empfang():
    data = request.get_json()
    if data:
        data["timestamp"] = datetime.now().isoformat()
        speichere_webhook_daten(data)
    return jsonify({"status": "ok"})

@app.route("/generate-testdata")
def generate_testdata():
    symbole = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "DOGEUSDT"]
    start = datetime.now() - timedelta(days=365)
    eintraege = []

    for _ in range(300):
        eintraege.append({
            "timestamp": (start + timedelta(days=random.randint(0, 365))).isoformat(),
            "symbol": random.choice(symbole),
            "info": "test"
        })

    with open(LOG_DATEI, "w") as f:
        json.dump(eintraege, f, indent=2)

    return "Testdaten generiert."

@app.route("/dashboard")
def dashboard():
    if not os.path.exists(LOG_DATEI):
        return "Keine Daten vorhanden."

    with open(LOG_DATEI, "r") as f:
        daten = json.load(f)

    if not daten:
        return "Keine Eintraege."

    df = pd.DataFrame(daten)
    if df.empty or "timestamp" not in df.columns or "symbol" not in df.columns:
        return "Unzureichende Daten."

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["monat"] = df["timestamp"].dt.to_period("M").astype(str)
    pivot = df.groupby(["symbol", "monat"]).size().unstack(fill_value=0)

    html = """
    <html><head>
    <style>
        body { font-family: Arial; padding: 20px; }
        table { border-collapse: collapse; }
        th, td { padding: 6px 10px; text-align: center; border: 1px solid #ccc; }
        th { background-color: #eee; }
        td[data-count] {
            background-color: rgb(255, calc(255 - min(200, data-count * 8)), calc(255 - min(200, data-count * 16)));
        }
    </style>
    </head><body>
    <h2>Webhook-Alarm Heatmap</h2>
    <table><thead><tr><th>Symbol</th>{% for col in data.columns %}<th>{{ col }}</th>{% endfor %}</tr></thead>
    <tbody>
    {% for index, row in data.iterrows() %}
    <tr><td>{{ index }}</td>
    {% for val in row %}
        <td data-count="{{ val }}">{{ val }}</td>
    {% endfor %}
    </tr>
    {% endfor %}
    </tbody></table>
    </body></html>
    """
    return render_template_string(html, data=pivot)

def speichere_webhook_daten(payload):
    if not os.path.exists(LOG_DATEI):
        with open(LOG_DATEI, "w") as f:
            json.dump([], f)
    with open(LOG_DATEI, "r") as f:
        daten = json.load(f)
    daten.append(payload)
    with open(LOG_DATEI, "w") as f:
        json.dump(daten, f, indent=2)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
