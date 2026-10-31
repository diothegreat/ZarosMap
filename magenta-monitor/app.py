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
import time as _time

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
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
# Πίσω από το nginx του VPS: σωστή IP επισκέπτη / https
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=7 * 24 * 3600)

# Προστασία από δοκιμές κωδικών: 5 λάθος προσπάθειες ανά IP => κλείδωμα 10 λεπτά
FAILED: dict[str, list[float]] = {}
MAX_FAILS, LOCK_SECONDS = 5, 600


def _is_locked_out(ip: str) -> bool:
    now = _time.time()
    FAILED[ip] = [t for t in FAILED.get(ip, []) if now - t < LOCK_SECONDS]
    return len(FAILED[ip]) >= MAX_FAILS


def _is_direct_local() -> bool:
    """Αίτημα κατευθείαν από τον ίδιο υπολογιστή (όχι μέσω VPS/nginx)."""
    return request.headers.get("X-Forwarded-For") is None and request.remote_addr == "127.0.0.1"

# Χωρίς login: η σελίδα login και ό,τι χρειάζεται το Node-RED.
PUBLIC_ENDPOINTS = {"login", "static"}
# Για το Node-RED: χωρίς login, αλλά ΜΟΝΟ από τον ίδιο τον υπολογιστή (όχι μέσω VPS)
LOCAL_ONLY_ENDPOINTS = {"health", "shutdown"}


@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or session.get("user"):
        return None
    if request.endpoint in LOCAL_ONLY_ENDPOINTS and _is_direct_local():
        return None
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error="Απαιτείται σύνδεση"), 401
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    ip = request.remote_addr or "?"
    if request.method == "POST" and _is_locked_out(ip):
        error = "Πολλές λάθος προσπάθειες – δοκίμασε ξανά σε 10 λεπτά"
    elif request.method == "POST":
        user = request.form.get("username", "")
        pwd = request.form.get("password", "")
        if user == AUTH["username"] and check_password_hash(AUTH["password_hash"], pwd):
            FAILED.pop(ip, None)
            session.clear()
            session["user"] = user
            session.permanent = True
            nxt = request.args.get("next", "/")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
        FAILED.setdefault(ip, []).append(_time.time())
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
