"""
Magenta Monitor – τοπικός Flask server.

    http://127.0.0.1:5000   -> σελίδα με dropdown καναλιών

Ακούει ΜΟΝΟ στο localhost (127.0.0.1), οπότε δεν είναι προσβάσιμος από το δίκτυο.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from player import MagentaPlayer, PlayerError

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

cfg_path = BASE_DIR / "config.json"
if not cfg_path.exists():
    cfg_path = BASE_DIR / "config.example.json"
CONFIG = json.loads(cfg_path.read_text(encoding="utf-8"))

USER = os.environ.get("MAGENTA_USER", "")
PASS = os.environ.get("MAGENTA_PASS", "")
if not USER or not PASS:
    raise SystemExit("Λείπουν MAGENTA_USER / MAGENTA_PASS στο αρχείο .env")

HOST = "127.0.0.1"
PORT = int(os.environ.get("MAGENTA_PORT", "5000"))

app = Flask(__name__)
player = MagentaPlayer(CONFIG, USER, PASS)


@app.get("/")
def index():
    return render_template("index.html", channels=CONFIG["channels"])


@app.get("/api/health")
def health():
    return jsonify(ok=True)


@app.get("/api/status")
def status():
    return jsonify(player.state())


@app.post("/api/play")
def play():
    channel_id = (request.get_json(silent=True) or {}).get("channel") or request.form.get("channel")
    try:
        player.play(channel_id)
    except PlayerError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    return jsonify(ok=True)


@app.post("/api/mute")
def mute():
    muted = bool((request.get_json(silent=True) or {}).get("muted", True))
    return jsonify(player.set_muted(muted))


@app.post("/api/volume")
def volume():
    vol = (request.get_json(silent=True) or {}).get("volume", 50)
    return jsonify(player.set_volume(vol))


@app.post("/api/fullscreen")
def fullscreen():
    return jsonify(player.toggle_fullscreen())


@app.post("/api/stop")
def stop():
    return jsonify(player.stop())


@app.post("/api/shutdown")
def shutdown():
    """Κλείνει Chrome + server (χρησιμοποιείται από το Node-RED)."""
    def _bye():
        player.stop()
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=_bye, daemon=True).start()
    return jsonify(ok=True)


if __name__ == "__main__":
    print(f"Magenta Monitor στο http://{HOST}:{PORT}", flush=True)
    app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)
