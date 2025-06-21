from flask import Flask, render_template, jsonify
import json
import os
from datetime import datetime
import pandas as pd

app = Flask(__name__)

@app.route("/")
def index():
    return "OK – Service läuft"

@app.route("/dashboard")
def dashboard():
    if not os.path.exists("webhook_logs.json"):
        return "Keine Daten vorhanden."

    with open("webhook_logs.json", "r") as f:
        daten = json.load(f)

    df = pd.DataFrame(daten)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["monat"] = df["timestamp"].dt.strftime("%Y-%m")
    
    matrix = df.groupby(["symbol", "monat"]).size().unstack(fill_value=0).to_dict()

    return render_template("dashboard.html", matrix=matrix)

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
