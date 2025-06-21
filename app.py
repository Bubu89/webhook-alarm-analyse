import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string
import pandas as pd

LOG_DATEI = "webhook_logs.json"

app = Flask(__name__, template_folder=".")

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
    table_html = pivot.to_html(classes="heatmap-table", border=0)

    return render_template_string(lade_heatmap_template(), table_html=table_html)

def speichere_webhook_daten(payload):
    if not os.path.exists(LOG_DATEI):
        with open(LOG_DATEI, "w") as f:
            json.dump([], f)
    with open(LOG_DATEI, "r") as f:
        daten = json.load(f)
    daten.append(payload)
    with open(LOG_DATEI, "w") as f:
        json.dump(daten, f, indent=2)

def lade_heatmap_template():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Webhook Heatmap Dashboard</title>
        <style>
            body { font-family: Arial, sans-serif; background: #f7f7f7; padding: 20px; }
            h1 { color: #333; }
            .heatmap-table { border-collapse: collapse; width: 100%; }
            .heatmap-table th, .heatmap-table td {
                border: 1px solid #ccc;
                text-align: center;
                padding: 8px;
            }
            .heatmap-table th {
                background-color: #f0f0f0;
            }
            .heatmap-table td {
                background-color: #ffffff;
            }
        </style>
        <script>
            window.onload = function () {
                const cells = document.querySelectorAll(".heatmap-table td");
                let max = 0;
                cells.forEach(cell => {
                    const val = parseInt(cell.innerText);
                    if (!isNaN(val) && val > max) max = val;
                });
                cells.forEach(cell => {
                    const val = parseInt(cell.innerText);
                    if (!isNaN(val)) {
                        const intensity = val / max;
                        const red = 255 - Math.round(255 * intensity);
                        const green = 255 - Math.round(100 * intensity);
                        const blue = 255 - Math.round(200 * intensity);
                        cell.style.backgroundColor = `rgb(${red}, ${green}, ${blue})`;
                    }
                });
            }
        </script>
    </head>
    <body>
        <h1>Webhook Monatsanalyse (Heatmap)</h1>
        {{ table_html|safe }}
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
