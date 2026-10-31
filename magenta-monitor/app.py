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
import secrets

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from player import MagentaPlayer, PlayerError

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

cfg_path = BASE_DIR / "config.json"
if not cfg_path.exists() or "REPLACE-ME" in cfg_path.read_text(encoding="utf-8"):
    # Δεν υπάρχει config.json ή είναι η παλιά (γερμανική) έκδοση -> πάρε το νέο πρότυπο.
    if cfg_path.exists():
        print("Το config.json είναι παλιό – αντικαθίσταται από το config.example.json", flush=True)
    cfg_path.write_text((BASE_DIR / "config.example.json").read_text(encoding="utf-8"), encoding="utf-8")
CONFIG = json.loads(cfg_path.read_text(encoding="utf-8"))
# Προφίλ Magenta TV ("Ποιος βλέπει;"): από config.json ή από MAGENTA_PROFILE στο .env
if not CONFIG.get("profile"):
    CONFIG["profile"] = os.environ.get("MAGENTA_PROFILE", "")

USER = os.environ.get("MAGENTA_USER", "")
PASS = os.environ.get("MAGENTA_PASS", "")
if not USER or not PASS:
    raise SystemExit("Λείπουν MAGENTA_USER / MAGENTA_PASS στο αρχείο .env")

HOST = "127.0.0.1"
PORT = int(os.environ.get("MAGENTA_PORT", "5000"))

# ------------------------------------------------------------------ auth ----
# Στοιχεία εισόδου στη σελίδα του server. Αρχικά admin / admin· αλλάζουν από τη
# σελίδα "Αλλαγή κωδικού" και σώζονται (κρυπτογραφημένα) στο web_auth.json.
AUTH_FILE = BASE_DIR / "web_auth.json"


def load_auth() -> dict:
    if AUTH_FILE.exists():
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    else:
        data = {"username": "admin", "password_hash": generate_password_hash("admin")}
    if not data.get("secret_key"):
        data["secret_key"] = secrets.token_hex(32)
        save_auth(data)
    return data


def save_auth(data: dict) -> None:
    AUTH_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


AUTH = load_auth()

app = Flask(__name__)
app.secret_key = AUTH["secret_key"]

# Χωρίς login: η σελίδα login και ό,τι χρειάζεται το Node-RED.
PUBLIC_ENDPOINTS = {"login", "static", "health", "shutdown"}


@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or session.get("user"):
        return None
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error="Απαιτείται σύνδεση"), 401
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user = request.form.get("username", "")
        pwd = request.form.get("password", "")
        if user == AUTH["username"] and check_password_hash(AUTH["password_hash"], pwd):
            session.clear()
            session["user"] = user
            session.permanent = True
            nxt = request.args.get("next", "/")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
        error = "Λάθος όνομα χρήστη ή κωδικός"
    return render_template("login.html", error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
def account():
    msg, error = None, None
    if request.method == "POST":
        cur = request.form.get("current", "")
        new_user = request.form.get("username", "").strip() or AUTH["username"]
        new_pwd = request.form.get("new", "")
        if not check_password_hash(AUTH["password_hash"], cur):
            error = "Ο τρέχων κωδικός είναι λάθος"
        elif len(new_pwd) < 4:
            error = "Ο νέος κωδικός πρέπει να έχει τουλάχιστον 4 χαρακτήρες"
        elif new_pwd != request.form.get("confirm", ""):
            error = "Οι δύο νέοι κωδικοί δεν ταιριάζουν"
        else:
            AUTH["username"] = new_user
            AUTH["password_hash"] = generate_password_hash(new_pwd)
            save_auth(AUTH)
            session["user"] = new_user
            msg = "Τα στοιχεία άλλαξαν"
    return render_template("account.html", username=AUTH["username"], msg=msg, error=error,
                           default_pwd=check_password_hash(AUTH["password_hash"], "admin"))

player = MagentaPlayer(CONFIG, USER, PASS)


@app.get("/")
def index():
    return render_template("index.html", channels=CONFIG["channels"],
                           default_pwd=check_password_hash(AUTH["password_hash"], "admin"))


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
