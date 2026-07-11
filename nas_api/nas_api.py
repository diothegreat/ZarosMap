#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NAS API server για το TAXIS Workbench — πόρτα 8787.

Ανακατασκευή του server που έτρεχε στο Synology NAS (192.168.1.21 /
Tailscale 100.77.230.44). Σερβίρει στοιχεία πελατών από το
corrected_output_file.json του γραφείο2 και κρατά ιστορικό ενεργειών
(audit events) σε SQLite δίπλα στο script.

Χρησιμοποιεί ΜΟΝΟ Python stdlib — δεν χρειάζεται pip install.

Πελάτες αυτού του API:
  * TAXIS Workbench (desktop GUI στα Windows)
  * Office Control Panel (FastAPI container στο VPS, routes/taxis.py)

Ρυθμίσεις μέσω environment (προαιρετικά):
  NAS_API_HOST, NAS_API_PORT, NAS_API_DATA, NAS_API_DB,
  NAS_API_TOKEN_FILE, NAS_API_MIN_FOLDER
"""

import hmac
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HOST = os.environ.get("NAS_API_HOST", "0.0.0.0")
PORT = int(os.environ.get("NAS_API_PORT", "8787"))
DATA_PATH = os.environ.get(
    "NAS_API_DATA",
    "/volume2/γραφείο2/Aρχείο γραφείου/corrected_output_file.json",
)
DB_PATH = os.environ.get("NAS_API_DB", os.path.join(BASE_DIR, "nas_api_events.db"))
TOKEN_FILE = os.environ.get("NAS_API_TOKEN_FILE", os.path.join(BASE_DIR, "internal_token.txt"))
# Οι "ενεργοί" πελάτες είναι οι φάκελοι από αυτόν τον αριθμό και πάνω
# (ίδια λογική με το nas_list_ready.py).
MIN_ACTIVE_FOLDER = int(os.environ.get("NAS_API_MIN_FOLDER", "6000"))


def load_token():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


INTERNAL_TOKEN = load_token()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def strip_accents(text):
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    ).upper()


def fix_encoding(text):
    """Επιδιόρθωση ελληνικών από clients που δεν κάνουν σωστό URL-encoding
    (τα UTF-8 bytes φτάνουν εδώ διαβασμένα ως latin-1)."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


# ── Δεδομένα πελατών ────────────────────────────────────────────────────────

class ClientData:
    """Φορτώνει το corrected_output_file.json και το ξαναδιαβάζει αυτόματα
    όταν αλλάξει στο δίσκο (έλεγχος mtime σε κάθε πρόσβαση)."""

    def __init__(self, path):
        self.path = path
        self.mtime = None
        self.clients = {}

    @staticmethod
    def _s(x):
        return "" if x is None else str(x).strip()

    def _normalize(self, folder, value):
        if isinstance(value, list) and value:
            value = value[0]
        if not isinstance(value, dict):
            return None
        user = self._s(value.get("USERNAME"))
        user = "" if user.upper() == "USERNAME" else user
        pw = self._s(value.get("PASSWORD"))
        pw = "" if pw.upper() == "PASSWORD" else pw
        amka = self._s(value.get("ΑΜΚΑ"))
        name = self._s(value.get("ΠΕΛΑΤΕΣ - ΔΙΑΔΙΚΟΙ"))
        missing = []
        if not amka:
            missing.append("ΑΜΚΑ")
        if not user:
            missing.append("USERNAME")
        if not pw:
            missing.append("PASSWORD")
        return {
            "folder_number": str(folder),
            "client_name": name,
            "username": user,
            "password": pw,
            "amka": amka,
            "ready": not missing,
            "missing": missing,
            "search_key": strip_accents(name),
        }

    def refresh(self, force=False):
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            self.clients = {}
            self.mtime = None
            return
        if not force and mtime == self.mtime:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError) as exc:
            print(f"{now_str()} ΣΦΑΛΜΑ ανάγνωσης {self.path}: {exc}", flush=True)
            return
        clients = {}
        for key, value in raw.items():
            entry = self._normalize(key, value)
            if entry is not None:
                clients[str(key)] = entry
        self.clients = clients
        self.mtime = mtime
        print(f"{now_str()} Φορτώθηκαν {len(clients)} πελάτες από {self.path}", flush=True)

    def get(self, folder):
        self.refresh()
        return self.clients.get(str(folder))

    def active(self):
        """Φάκελοι >= MIN_ACTIVE_FOLDER, μόνο αριθμητικοί."""
        self.refresh()
        out = []
        for key, entry in self.clients.items():
            if key.isdigit() and int(key) >= MIN_ACTIVE_FOLDER:
                out.append(entry)
        out.sort(key=lambda e: int(e["folder_number"]))
        return out

    def search(self, query, limit):
        self.refresh()
        query = query.strip()
        results = []
        if not query:
            return results
        needle = strip_accents(query)
        for key in sorted(self.clients, key=lambda k: (not k.isdigit(), -int(k) if k.isdigit() else 0)):
            entry = self.clients[key]
            if key.startswith(query) or (needle and needle in entry["search_key"]):
                results.append({
                    "folder_number": entry["folder_number"],
                    "client_name": entry["client_name"],
                })
                if len(results) >= limit:
                    break
        return results


DATA = ClientData(DATA_PATH)


# ── Βάση συμβάντων (SQLite) ─────────────────────────────────────────────────

def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_number TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT '',
                service_code TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL DEFAULT 1,
                error_message TEXT NOT NULL DEFAULT '',
                workstation_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )


def meta_get(key, default=""):
    with db() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def meta_set(key, value):
    with db() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def event_row_to_dict(row):
    return {
        "folder_number": row[0],
        "action": row[1],
        "service_code": row[2],
        "success": bool(row[3]),
        "error_message": row[4],
        "workstation_name": row[5],
        "created_at": row[6],
    }


EVENT_COLS = "folder_number, action, service_code, success, error_message, workstation_name, created_at"


# ── HTTP handler ────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "TaxisNasApi/2.0"

    # -- βοηθητικά ----------------------------------------------------------

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def check_token(self):
        if not INTERNAL_TOKEN:
            self.send_json({"detail": "Δεν έχει ρυθμιστεί internal token στον server "
                                      "(λείπει το internal_token.txt)."}, 503)
            return False
        supplied = self.headers.get("X-Internal-Token", "")
        if not hmac.compare_digest(supplied, INTERNAL_TOKEN):
            self.send_json({"detail": "Μη έγκυρο internal token."}, 401)
            return False
        return True

    def query_int(self, qs, name, default):
        try:
            return max(1, min(int(qs.get(name, [default])[0]), 5000))
        except (ValueError, TypeError):
            return default

    def read_body_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def log_message(self, fmt, *args):
        print(f"{now_str()} {self.address_string()} {fmt % args}", flush=True)

    # -- routes -------------------------------------------------------------

    def do_GET(self):
        url = urlparse(self.path)
        path = unquote(url.path).rstrip("/") or "/"
        qs = parse_qs(url.query)

        if path == "/health":
            return self.send_json({"status": "ok", "time": now_str()})

        if path == "/dashboard/stats":
            return self.dashboard_stats()

        m = re.fullmatch(r"/dashboard/client-history/([^/]+)", path)
        if m:
            return self.client_history(m.group(1), self.query_int(qs, "limit", 10))

        if path == "/dashboard/missing-clients":
            return self.missing_clients(self.query_int(qs, "limit", 120))

        if path == "/dashboard/recent-worked":
            return self.recent_worked(self.query_int(qs, "limit", 40))

        if path == "/dashboard/problem-logins":
            return self.problem_logins(self.query_int(qs, "limit", 40))

        if path == "/dashboard/latest-folders":
            return self.latest_folders(self.query_int(qs, "limit", 40))

        m = re.fullmatch(r"/credentials/([^/]+)/readiness", path)
        if m:
            return self.readiness(m.group(1))

        if path == "/clients/search":
            query = fix_encoding(qs.get("q", [""])[0])
            return self.send_json(DATA.search(query, self.query_int(qs, "limit", 30)))

        m = re.fullmatch(r"/clients/([^/]+)", path)
        if m:
            return self.client_details(m.group(1))

        m = re.fullmatch(r"/internal/launch-payload/([^/]+)", path)
        if m:
            if not self.check_token():
                return
            return self.launch_payload(m.group(1))

        self.send_json({"detail": "Not Found"}, 404)

    def do_POST(self):
        path = unquote(urlparse(self.path).path).rstrip("/")

        if path == "/internal/audit/event":
            if not self.check_token():
                return
            return self.audit_event()

        if path == "/dashboard/recent-worked/clear":
            if not self.check_token():
                return
            meta_set("recent_cleared_at", now_str())
            return self.send_json({"status": "ok", "cleared_at": now_str()})

        if path == "/internal/import/excel":
            if not self.check_token():
                return
            DATA.refresh(force=True)
            meta_set("last_sync", now_str())
            return self.send_json({
                "status": "ok",
                "clients": len(DATA.clients),
                "note": "Ξαναδιαβάστηκε το corrected_output_file.json από το δίσκο.",
            })

        self.send_json({"detail": "Not Found"}, 404)

    # -- υλοποιήσεις ---------------------------------------------------------

    def client_public(self, entry):
        """Στοιχεία πελάτη ΧΩΡΙΣ password (το password δίνεται μόνο από το
        /internal/launch-payload με token)."""
        return {
            "folder_number": entry["folder_number"],
            "folder": entry["folder_number"],
            "client_name": entry["client_name"],
            "name": entry["client_name"],
            "amka": entry["amka"],
            "afm": "",
            "phone": "",
            "notes": "",
            "appointment": "",
            "username": entry["username"],
            "ready": entry["ready"],
            "missing": entry["missing"],
        }

    def client_details(self, folder):
        entry = DATA.get(folder)
        if entry is None:
            return self.send_json({"detail": f"Δεν βρέθηκε φάκελος {folder}"}, 404)
        self.send_json(self.client_public(entry))

    def readiness(self, folder):
        entry = DATA.get(folder)
        if entry is None:
            return self.send_json({"detail": f"Δεν βρέθηκε φάκελος {folder}"}, 404)
        self.send_json({
            "folder_number": entry["folder_number"],
            "client_name": entry["client_name"],
            "ready": entry["ready"],
            "missing": entry["missing"],
            "has_amka": bool(entry["amka"]),
            "has_username": bool(entry["username"]),
            "has_password": bool(entry["password"]),
        })

    def launch_payload(self, folder):
        entry = DATA.get(folder)
        if entry is None:
            return self.send_json({"detail": f"Δεν βρέθηκε φάκελος {folder}"}, 404)
        self.send_json({
            "folder": entry["folder_number"],
            "folder_number": entry["folder_number"],
            "name": entry["client_name"],
            "client_name": entry["client_name"],
            "username": entry["username"],
            "password": entry["password"],
            "amka": entry["amka"],
            "ready": entry["ready"],
            "missing": entry["missing"],
        })

    def dashboard_stats(self):
        active = DATA.active()
        today = datetime.now().strftime("%Y-%m-%d")
        with db() as conn:
            worked_today = conn.execute(
                "SELECT COUNT(DISTINCT folder_number) FROM events WHERE created_at LIKE ?",
                (today + "%",),
            ).fetchone()[0]
        last_sync = meta_get("last_sync")
        if not last_sync and DATA.mtime:
            last_sync = datetime.fromtimestamp(DATA.mtime).strftime("%Y-%m-%d %H:%M:%S")
        self.send_json({
            "total_active_clients": len(active),
            "ready_clients": sum(1 for e in active if e["ready"]),
            "missing_amka": sum(1 for e in active if not e["amka"]),
            "worked_today": worked_today,
            "last_sync": {"created_at": last_sync.replace(" ", "T") if last_sync else ""},
        })

    def client_history(self, folder, limit):
        with db() as conn:
            rows = conn.execute(
                f"SELECT {EVENT_COLS} FROM events WHERE folder_number=? "
                "ORDER BY id DESC LIMIT ?",
                (str(folder), limit),
            ).fetchall()
        self.send_json({"folder_number": str(folder),
                        "events": [event_row_to_dict(r) for r in rows]})

    def missing_clients(self, limit):
        rows = [
            {
                "folder_number": e["folder_number"],
                "client_name": e["client_name"],
                "missing": e["missing"],
            }
            for e in reversed(DATA.active())
            if e["missing"]
        ]
        self.send_json(rows[:limit])

    def recent_worked(self, limit):
        cleared = meta_get("recent_cleared_at")
        with db() as conn:
            rows = conn.execute(
                "SELECT folder_number, MAX(created_at) AS last_used_at FROM events "
                "WHERE folder_number != '' AND created_at > ? "
                "GROUP BY folder_number ORDER BY last_used_at DESC LIMIT ?",
                (cleared, limit),
            ).fetchall()
        DATA.refresh()
        out = []
        for folder, last_used in rows:
            entry = DATA.clients.get(folder)
            out.append({
                "folder_number": folder,
                "client_name": entry["client_name"] if entry else "",
                "last_used_at": last_used,
            })
        self.send_json(out)

    def problem_logins(self, limit):
        with db() as conn:
            rows = conn.execute(
                f"SELECT {EVENT_COLS} FROM events WHERE success=0 "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        DATA.refresh()
        out = []
        for row in rows:
            item = event_row_to_dict(row)
            entry = DATA.clients.get(item["folder_number"])
            item["client_name"] = entry["client_name"] if entry else ""
            out.append(item)
        self.send_json(out)

    def latest_folders(self, limit):
        active = DATA.active()
        rows = [
            {
                "folder_number": e["folder_number"],
                "client_name": e["client_name"],
                "updated_at": "",
            }
            for e in reversed(active[-limit:])
        ]
        self.send_json(rows)

    def audit_event(self):
        body = self.read_body_json()
        workstation = (
            str(body.get("workstation_name") or "").strip()
            or self.headers.get("X-Workstation-Name", "").strip()
            or self.address_string()
        )
        with db() as conn:
            conn.execute(
                f"INSERT INTO events ({EVENT_COLS}) VALUES (?,?,?,?,?,?,?)",
                (
                    str(body.get("folder_number") or "").strip(),
                    str(body.get("action") or "").strip(),
                    str(body.get("service_code") or "").strip(),
                    1 if body.get("success", True) else 0,
                    str(body.get("error_message") or "").strip(),
                    workstation,
                    now_str(),
                ),
            )
        self.send_json({"status": "ok"})


def main():
    init_db()
    DATA.refresh()
    if not INTERNAL_TOKEN:
        print(f"{now_str()} ΠΡΟΣΟΧΗ: δεν βρέθηκε {TOKEN_FILE} — τα /internal/* "
              "endpoints θα απαντούν 503 μέχρι να δημιουργηθεί.", flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"{now_str()} NAS API ξεκίνησε στο http://{HOST}:{PORT} "
          f"(δεδομένα: {DATA_PATH}, {len(DATA.clients)} πελάτες)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
