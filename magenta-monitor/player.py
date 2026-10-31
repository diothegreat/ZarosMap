"""
Έλεγχος του Google Chrome μέσω Playwright.

Όλες οι κλήσεις Playwright γίνονται σε ΕΝΑ δικό τους thread (το Playwright sync
API δεν είναι thread-safe), και ο Flask στέλνει εντολές μέσω ουράς.

Ροή:  play(channel) -> άνοιγμα Chrome -> URL καναλιού -> (αν χρειαστεί) login
      -> ξανά URL καναλιού -> αναμονή για <video> -> "κλείδωμα" της σελίδας.

"Κλείδωμα" = ένα διάφανο layer πάνω από όλη τη σελίδα που τρώει όλα τα clicks,
το scroll, το δεξί κλικ και τα πλήκτρα. Επιτρέπονται ΜΟΝΟ: ήχος (mute / ένταση)
και πλήρης οθόνη. Ένας watchdog γυρίζει πίσω στο κανάλι αν η σελίδα φύγει.
"""

from __future__ import annotations

import os
import queue
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page, sync_playwright

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "chrome-profile"  # κρατάει cookies -> σπάνια ξανά login

# --------------------------------------------------------------------------- #
# JavaScript που "κλειδώνει" τη σελίδα. Είναι idempotent (ασφαλές να ξανατρέξει).
# --------------------------------------------------------------------------- #
LOCK_JS = r"""
(() => {
  if (document.getElementById('mm-shield')) return true;

  const pickVideo = () => {
    const vids = [...document.querySelectorAll('video')];
    if (!vids.length) return null;
    return vids.sort((a, b) =>
      (b.clientWidth * b.clientHeight) - (a.clientWidth * a.clientHeight))[0];
  };

  const style = document.createElement('style');
  style.id = 'mm-style';
  style.textContent = `
    html, body { overflow: hidden !important; background: #000 !important; }
    video.mm-video {
      position: fixed !important; inset: 0 !important;
      width: 100vw !important; height: 100vh !important;
      max-width: none !important; max-height: none !important;
      object-fit: contain !important; background: #000 !important;
      z-index: 2147483646 !important; transform: none !important;
    }
    #mm-shield {
      position: fixed; inset: 0; z-index: 2147483647;
      background: transparent; cursor: none;
    }
    #mm-shield.mm-active { cursor: default; }
    #mm-bar {
      position: absolute; left: 50%; bottom: 24px; transform: translateX(-50%);
      display: flex; gap: 12px; align-items: center;
      padding: 10px 16px; border-radius: 12px;
      background: rgba(0,0,0,.65); color: #fff;
      font: 14px/1 system-ui, sans-serif;
      opacity: 0; transition: opacity .25s;
    }
    #mm-shield.mm-active #mm-bar { opacity: 1; }
    #mm-bar button {
      all: unset; cursor: pointer; padding: 6px 10px; border-radius: 8px;
      background: rgba(255,255,255,.15); font-size: 16px;
    }
    #mm-bar button:hover { background: rgba(255,255,255,.3); }
    #mm-bar input[type=range] { width: 140px; cursor: pointer; }
    #mm-title { opacity: .8; padding-right: 6px; }
  `;
  document.documentElement.appendChild(style);

  const shield = document.createElement('div');
  shield.id = 'mm-shield';
  shield.innerHTML = `
    <div id="mm-bar">
      <span id="mm-title"></span>
      <button id="mm-mute" title="Ήχος (M)">🔊</button>
      <input id="mm-vol" type="range" min="0" max="100" step="1" title="Ένταση">
      <button id="mm-fs" title="Πλήρης οθόνη (F)">⛶</button>
    </div>`;
  document.documentElement.appendChild(shield);
  shield.querySelector('#mm-title').textContent = window.__mmTitle || '';

  const muteBtn = shield.querySelector('#mm-mute');
  const vol = shield.querySelector('#mm-vol');
  const fsBtn = shield.querySelector('#mm-fs');

  const sync = () => {
    const v = pickVideo();
    if (!v) return;
    if (!v.classList.contains('mm-video')) v.classList.add('mm-video');
    v.controls = false;
    muteBtn.textContent = (v.muted || v.volume === 0) ? '🔇' : '🔊';
    if (document.activeElement !== vol) vol.value = Math.round(v.volume * 100);
  };
  window.__mmSync = sync;
  setInterval(sync, 500);
  sync();

  const toggleMute = () => { const v = pickVideo(); if (v) { v.muted = !v.muted; sync(); } };
  const toggleFs = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else document.documentElement.requestFullscreen().catch(() => {});
  };

  muteBtn.addEventListener('click', e => { e.stopPropagation(); toggleMute(); });
  fsBtn.addEventListener('click', e => { e.stopPropagation(); toggleFs(); });
  vol.addEventListener('input', e => {
    e.stopPropagation();
    const v = pickVideo();
    if (v) { v.volume = vol.value / 100; v.muted = (v.volume === 0); sync(); }
  });
  shield.addEventListener('dblclick', e => { if (e.target === shield) toggleFs(); });

  // Εμφάνιση μπάρας όταν κουνιέται το ποντίκι
  let hideT;
  shield.addEventListener('mousemove', () => {
    shield.classList.add('mm-active');
    clearTimeout(hideT);
    hideT = setTimeout(() => shield.classList.remove('mm-active'), 2500);
  });

  // Μπλοκάρισμα όλων των υπόλοιπων αλληλεπιδράσεων
  const block = e => {
    if (e.target && e.target.closest && e.target.closest('#mm-bar')) return;
    e.preventDefault(); e.stopImmediatePropagation();
  };
  ['click','mousedown','mouseup','pointerdown','pointerup','auxclick',
   'contextmenu','wheel','touchstart','touchend','dragstart','selectstart']
    .forEach(t => window.addEventListener(t, block, { capture: true, passive: false }));

  // Πληκτρολόγιο: επιτρέπονται μόνο M (mute), F (fullscreen), ↑/↓ (ένταση), Esc
  const keys = e => {
    const k = e.key;
    if (k === 'Escape' || k === 'F11') return;           // έξοδος από πλήρη οθόνη
    e.preventDefault(); e.stopImmediatePropagation();
    if (e.type !== 'keydown' || e.repeat && !(k === 'ArrowUp' || k === 'ArrowDown')) return;
    const v = pickVideo();
    if (k === 'm' || k === 'M') toggleMute();
    else if (k === 'f' || k === 'F') toggleFs();
    else if (v && k === 'ArrowUp')   { v.muted = false; v.volume = Math.min(1, v.volume + .05); sync(); }
    else if (v && k === 'ArrowDown') { v.volume = Math.max(0, v.volume - .05); sync(); }
  };
  ['keydown','keyup','keypress'].forEach(t =>
    window.addEventListener(t, keys, { capture: true }));

  return true;
})()
"""

VIDEO_PLAYING_JS = r"""
(() => {
  const v = [...document.querySelectorAll('video')]
    .find(v => v.readyState >= 2 && v.clientWidth > 100);
  return !!v;
})()
"""


# Κάνει scroll κάτω τη σελίδα ΚΑΙ κάθε εσωτερικό scrollable container. true = τέλος.
SCROLL_JS = r"""
(() => {
  let moved = false;
  const els = [document.scrollingElement, ...document.querySelectorAll('*')].filter(e => {
    if (!e || e.scrollHeight <= e.clientHeight + 5) return false;
    if (e === document.scrollingElement) return true;
    const oy = getComputedStyle(e).overflowY;
    return oy === 'auto' || oy === 'scroll';
  });
  for (const e of els) {
    const before = e.scrollTop;
    e.scrollTop = before + Math.max(300, e.clientHeight * 0.8);
    if (e.scrollTop !== before) moved = true;
  }
  return !moved;
})()
"""

# Όλα τα "ονόματα" που φαίνονται στη σελίδα (για το αρχείο διάγνωσης).
LABELS_JS = r"""
(() => {
  const rows = [];
  const add = (kind, v, el) => {
    v = (v || '').trim().replace(/\s+/g, ' ');
    if (!v || v.length > 120) return;
    rows.push(`${kind}\t${v}\t<${el.tagName.toLowerCase()} class="${el.className}">`);
  };
  document.querySelectorAll('img[alt]').forEach(e => add('alt', e.alt, e));
  document.querySelectorAll('[title]').forEach(e => add('title', e.title, e));
  document.querySelectorAll('[aria-label]').forEach(e => add('aria', e.getAttribute('aria-label'), e));
  document.querySelectorAll('img[src]').forEach(e => add('src', e.src, e));
  document.querySelectorAll('[style*="background"]').forEach(e => {
    const m = (e.getAttribute('style') || '').match(/url\(["']?([^"')]+)/);
    if (m) add('bg', m[1], e);
  });
  document.querySelectorAll('a[href]').forEach(e => add('href', e.getAttribute('href'), e));
  document.querySelectorAll('body *').forEach(e => {
    if (e.children.length === 0) add('text', e.textContent, e);
  });
  return rows;
})()
"""


# Ενσωματωμένοι selectors για το streaming.magentatv.gr. Προστίθενται ΜΕΤΑ από
# αυτούς του config.json, οπότε δεν χρειάζεται να ενημερώνεις το config.
DEFAULT_SELECTORS: dict[str, list[str]] = {
    "cookie_accept": [
        "button.acceptAll", "button:has-text('Ενημερώθηκα')", "button:has-text('I have been informed')",
        "button:has-text('Accept')",
    ],
    "menu": [
        ".nav-burger", "[aria-label='Μενού']", "[aria-label='Menu']", ".navbar-toggler",
    ],
    "login_entry": [
        "text=/^\\s*(Σύνδεση|Είσοδος|Sign ?in|Log ?in|Login)\\s*$/i",
        "button:has-text('Sign in')", "a:has-text('Sign in')",
        "button:has-text('Log in')", "a:has-text('Log in')",
    ],
    "username": ["input[type='email']", "input[name*='user' i]", "input[name*='mail' i]",
                 "input[id*='user' i]", "input[type='text']"],
    "password": ["input[type='password']"],
    "username_next": ["button:has-text('Next')", "button:has-text('Continue')", "button[type='submit']"],
    "password_submit": ["button:has-text('Sign in')", "button:has-text('Log in')", "button[type='submit']",
                        "input[type='submit']"],
    # Υπάρχει μόνο όταν είσαι συνδεδεμένος (avatar προφίλ πάνω δεξιά)
    "logged_in": ["img.nav-avatar", ".ic2-notifications"],
    "channel_tile": [".channels-channel"],
}


class PlayerError(RuntimeError):
    pass


class MagentaPlayer:
    """Τρέχει σε δικό του thread. Ο Flask καλεί τις public μεθόδους."""

    def __init__(self, config: dict, username: str, password: str):
        self.cfg = config
        self.username = username
        self.password = password
        self.channels = {c["id"]: c for c in config["channels"]}
        sel = config.setdefault("selectors", {})
        for key, defaults in DEFAULT_SELECTORS.items():
            cur = [x for x in sel.get(key, []) if isinstance(x, str)]
            sel[key] = cur + [d for d in defaults if d not in cur]

        self._q: "queue.Queue[tuple[str, Any, queue.Queue | None]]" = queue.Queue()
        self._lock = threading.Lock()
        self._state = {"status": "idle", "channel": None, "message": "Αναμονή επιλογής καναλιού"}

        self._pw = None
        self._ctx = None
        self._browser = None
        self._proc: subprocess.Popen | None = None
        self._seen_labels: dict[str, None] = {}
        self._page: Page | None = None
        self._locked = False
        self._channel_url: str | None = None
        self._current: dict | None = None

        self._thread = threading.Thread(target=self._run, name="playwright", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ API --
    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def play(self, channel_id: str) -> None:
        if channel_id not in self.channels:
            raise PlayerError(f"Άγνωστο κανάλι: {channel_id}")
        self._q.put(("play", channel_id, None))

    def set_muted(self, muted: bool) -> dict:
        return self._call("mute", muted)

    def set_volume(self, volume: int) -> dict:
        return self._call("volume", max(0, min(100, int(volume))))

    def toggle_fullscreen(self) -> dict:
        return self._call("fullscreen", None)

    def stop(self) -> dict:
        return self._call("stop", None, timeout=20)

    # ------------------------------------------------------------ internals --
    def _call(self, cmd: str, arg: Any, timeout: float = 10) -> dict:
        reply: queue.Queue = queue.Queue()
        self._q.put((cmd, arg, reply))
        try:
            ok, data = reply.get(timeout=timeout)
        except queue.Empty:
            return {"ok": False, "error": "timeout (το κανάλι μάλλον ακόμα φορτώνει)"}
        return {"ok": ok, **({"data": data} if ok else {"error": data})}

    def _set(self, status: str, message: str, channel: str | None = ...) -> None:  # type: ignore[assignment]
        with self._lock:
            self._state["status"] = status
            self._state["message"] = message
            if channel is not ...:
                self._state["channel"] = channel
        print(f"[player] {status}: {message}", flush=True)

    def _run(self) -> None:
        self._pw = sync_playwright().start()
        while True:
            try:
                cmd, arg, reply = self._q.get(timeout=1.0)
            except queue.Empty:
                self._watchdog()
                continue
            try:
                result = getattr(self, f"_do_{cmd}")(arg)
                if reply:
                    reply.put((True, result))
            except Exception as exc:  # noqa: BLE001
                if reply:
                    reply.put((False, str(exc)))
                if cmd == "play":
                    # Σε σφάλμα εμφάνισε το παράθυρο (π.χ. για κωδικό SMS / captcha).
                    try:
                        self._show_window()
                    except Exception:  # noqa: BLE001
                        pass
                    self._locked = False
                    self._set("error", f"Σφάλμα: {exc}")

    # ---------------------------------------------------------- browser ----
    def _find_chrome(self) -> str:
        if self.cfg.get("chrome_path"):
            return self.cfg["chrome_path"]
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            "/usr/bin/google-chrome", "/opt/google/chrome/chrome",
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        found = shutil.which("chrome") or shutil.which("google-chrome")
        if found:
            return found
        raise PlayerError("Δεν βρέθηκε το Google Chrome – βάλε \"chrome_path\" στο config.json")

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _ensure_browser(self) -> Page:
        """Ανοίγει το κανονικό Chrome σε 'app' παράθυρο (χωρίς γραμμή διευθύνσεων/tabs)
        και συνδέεται σε αυτό μέσω CDP. Έτσι δεν υπάρχουν banners αυτοματισμού
        ('--no-sandbox', 'controlled by automated software') και δουλεύει το DRM."""
        if self._ctx is not None and self._page is not None and not self._page.is_closed():
            return self._page
        self._close_browser()
        w, h = self.cfg.get("window_size", [1280, 720])
        PROFILE_DIR.mkdir(exist_ok=True)
        port = self._free_port()
        self._proc = subprocess.Popen([
            self._find_chrome(),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={PROFILE_DIR}",
            f"--app={self.cfg.get('start_url', 'about:blank')}",
            f"--window-size={w},{h}",
            # Το παράθυρο ξεκινά εκτός οθόνης: login/πλοήγηση γίνονται αόρατα.
            *(["--window-position=-32000,-32000"] if self._hide else []),
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
            "--disable-background-media-suspend",
            "--autoplay-policy=no-user-gesture-required",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--disable-features=Translate,TranslateUI",
            *self.cfg.get("chrome_args", []),
        ])
        endpoint = f"http://127.0.0.1:{port}"
        deadline = time.time() + 20
        while True:
            try:
                urllib.request.urlopen(endpoint + "/json/version", timeout=1).read()
                break
            except OSError:
                if time.time() > deadline or self._proc.poll() is not None:
                    raise PlayerError("Το Chrome δεν άνοιξε (μήπως είναι ήδη ανοιχτό με το ίδιο προφίλ;)")
                time.sleep(0.3)
        self._browser = self._pw.chromium.connect_over_cdp(endpoint)
        self._ctx = self._browser.contexts[0]
        self._ctx.set_default_timeout(self.cfg["timeouts_ms"]["page"])
        for _ in range(40):
            pages = [p for p in self._ctx.pages if not p.url.startswith(("devtools:", "chrome-extension:"))]
            if pages:
                break
            time.sleep(0.25)
        self._page = pages[0] if pages else self._ctx.new_page()
        # Αν κάτι ανοίξει νέο παράθυρο/tab, το κλείνουμε.
        self._ctx.on("page", lambda p: p.close() if p is not self._page else None)
        if self._hide:
            self._set_window({"left": -32000, "top": -32000})
        return self._page

    @property
    def _hide(self) -> bool:
        return bool(self.cfg.get("hide_until_ready", True))

    def _set_window(self, bounds: dict) -> None:
        cdp = self._ctx.new_cdp_session(self._page)
        try:
            wid = cdp.send("Browser.getWindowForTarget")["windowId"]
            state = cdp.send("Browser.getWindowBounds", {"windowId": wid})["bounds"].get("windowState")
            if state != "normal":
                cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": bounds})
        finally:
            cdp.detach()

    def _show_window(self) -> None:
        """Φέρνει το παράθυρο στην οθόνη (μόλις το κανάλι είναι έτοιμο)."""
        if not self._hide or self._page is None or self._page.is_closed():
            return
        w, h = self.cfg.get("window_size", [1280, 720])
        x, y = self.cfg.get("window_position", [100, 100])
        self._set_window({"left": x, "top": y, "width": w, "height": h})
        self._page.bring_to_front()

    def _close_browser(self) -> None:
        try:
            if getattr(self, "_browser", None) is not None:
                self._browser.close()
        except PWError:
            pass
        proc = getattr(self, "_proc", None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None
        self._browser = None
        self._ctx = None
        self._page = None
        self._locked = False

    # ---------------------------------------------------------- helpers ----
    def _first_visible(self, page: Page, selectors: list[str], timeout_ms: int):
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            for sel in selectors:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible():
                        return loc
                except PWError:
                    pass
            page.wait_for_timeout(250)
        return None

    def _accept_cookies(self, page: Page) -> None:
        loc = self._first_visible(page, self.cfg["selectors"]["cookie_accept"], 3000)
        if loc:
            loc.click()
            page.wait_for_timeout(500)

    def _video_playing(self, page: Page) -> bool:
        try:
            return bool(page.evaluate(VIDEO_PLAYING_JS))
        except PWError:
            return False

    def _login(self, page: Page) -> None:
        s = self.cfg["selectors"]
        t = self.cfg["timeouts_ms"]["element"]
        self._set("login", "Σύνδεση με τους κωδικούς σου…")

        user = self._first_visible(page, s["username"], 1500)
        if not user:
            entry = self._first_visible(page, s["login_entry"], 3000)
            if not entry:
                # Στο Magenta TV GR το "Σύνδεση" είναι μέσα στο μενού ☰
                menu = self._first_visible(page, s["menu"], 3000)
                if menu:
                    menu.click()
                    page.wait_for_timeout(1000)
                entry = self._first_visible(page, s["login_entry"], t)
            if entry:
                entry.click()
                page.wait_for_load_state("domcontentloaded")
            self._accept_cookies(page)
            user = self._first_visible(page, s["username"], t)

        if user:
            user.fill(self.username)
            # Η σελίδα login της Telekom ζητά πρώτα username, μετά password.
            pwd = self._first_visible(page, s["password"], 1000)
            if not pwd:
                nxt = self._first_visible(page, s["username_next"], t)
                if nxt:
                    nxt.click()
                pwd = self._first_visible(page, s["password"], t)
        else:
            pwd = self._first_visible(page, s["password"], 1000)

        if not pwd:
            self._dump_debug(page, "login")
            raise PlayerError("Δεν βρέθηκε η φόρμα σύνδεσης (στοιχεία στον φάκελο debug: login.png / login.txt)")
        pwd.fill(self.password)
        btn = self._first_visible(page, s["password_submit"], t)
        if btn:
            btn.click()
        else:
            pwd.press("Enter")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(3000)
        if self._first_visible(page, s["password"], 1500):
            self._dump_debug(page, "login")
            raise PlayerError("Η σύνδεση απέτυχε – έλεγξε email/κωδικό στο .env (στοιχεία: debug/login.png)")

    def _is_logged_in(self, page: Page, timeout_ms: int) -> bool:
        return self._first_visible(page, self.cfg["selectors"]["logged_in"], timeout_ms) is not None

    def _wait_for_video(self, page: Page, timeout_ms: int) -> bool:
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if self._video_playing(page):
                return True
            page.wait_for_timeout(500)
        return False

    def _wait_video_or_login(self, page: Page, timeout_ms: int, ready: list[str] | None = None) -> str:
        """Περιμένει μέχρι να εμφανιστεί είτε video ("video") είτε φόρμα/κουμπί login ("login")."""
        s = self.cfg["selectors"]
        login_sel = s["username"] + s["password"] + s["login_entry"]
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if self._video_playing(page):
                return "video"
            if ready and self._first_visible(page, ready, 300) is not None:
                return "video"
            url = page.url.lower()
            if any(k in url for k in ("login", "signin", "sso", "auth", "idm.", "accounts.")):
                return "login"
            if self._first_visible(page, login_sel, 300) is not None:
                return "login"
            page.wait_for_timeout(500)
        return "unknown"

    def _lock_page(self) -> None:
        assert self._page is not None
        title = (self._current or {}).get("name", "")
        self._page.evaluate(f"window.__mmTitle = {title!r};")
        self._page.evaluate(LOCK_JS)
        self._locked = True

    # --------------------------------------------------------- commands ----
    def _do_play(self, channel_id: str) -> None:
        ch = self.channels[channel_id]
        self._current = ch
        self._locked = False
        direct = bool(ch.get("url"))
        target = ch["url"] if direct else self.cfg["channels_url"]
        tile_sel = self._tile_selectors(ch)
        self._seen_labels = {}

        self._set("starting", f"Άνοιγμα Chrome για {ch['name']}…", channel=channel_id)
        page = self._ensure_browser()
        if not self._hide:
            page.bring_to_front()

        self._set("loading", f"Φόρτωση {ch['name']}…")
        page.goto(target, wait_until="domcontentloaded")
        self._accept_cookies(page)

        # "Έτοιμη σελίδα" = εμφανίστηκαν κανάλια ή τουλάχιστον το μενού της εφαρμογής.
        ready = [] if direct else tile_sel + self.cfg["selectors"]["menu"]
        if self._wait_video_or_login(page, 20000, ready) == "login":
            self._login(page)
            self._after_login(page, target, ready)

        self._handle_profile(page, target, ready)

        if not direct and not self._is_logged_in(page, 6000):
            # Guest mode (π.χ. έληξε η σύνδεση): άδεια λίστα καναλιών, χωρίς avatar.
            self._login(page)
            self._after_login(page, target, ready)
            self._handle_profile(page, target, ready)

        if not direct:
            self._open_from_list(page, ch, tile_sel)

        self._set("loading", "Αναμονή για το video…")
        if not self._wait_for_video(page, 6000):
            play = self._first_visible(page, self.cfg["selectors"].get("play_button", []), 3000)
            if play:
                play.click()
            if not self._wait_for_video(page, self.cfg["timeouts_ms"]["video"]):
                raise PlayerError("Το video δεν ξεκίνησε. Έλεγξε το κανάλι στο config.json και τα στοιχεία σύνδεσης.")

        self._channel_url = page.url
        self._lock_page()
        self._show_window()
        self._set("playing", f"Παίζει: {ch['name']}")

    def _after_login(self, page: Page, target: str, ready: list[str]) -> None:
        name = self._current["name"] if self._current else ""
        self._set("loading", f"Μετάβαση στο {name}…")
        page.goto(target, wait_until="domcontentloaded")
        self._accept_cookies(page)
        self._wait_video_or_login(page, 15000, ready)

    def _handle_profile(self, page: Page, target: str, ready: list[str]) -> None:
        if self._select_profile(page):
            self._after_login(page, target, ready)

    def _select_profile(self, page: Page) -> bool:
        """Αν εμφανιστεί η οθόνη επιλογής προφίλ, πατάει το προφίλ από το config ("profile")."""
        name = (self.cfg.get("profile") or "").strip()
        if not name:
            return False
        n = name.replace('"', '\\"')
        sels = self.cfg["selectors"].get("profile") or [
            f'text=/^\\s*{n}\\s*$/i',
            f'[title="{n}" i]', f'[aria-label="{n}" i]', f'img[alt="{n}" i]',
        ]
        loc = self._first_visible(page, sels, 4000)
        if not loc:
            return False
        self._set("loading", f"Επιλογή προφίλ '{name}'…")
        loc.click()
        page.wait_for_timeout(2500)
        return True

    def _collect_labels(self, page: Page) -> None:
        try:
            for row in page.evaluate(LABELS_JS):
                self._seen_labels.setdefault(row, None)
        except PWError:
            pass

    def _dump_debug(self, page: Page, tag: str) -> None:
        """Σώζει screenshot + λίστα με τα ονόματα που υπάρχουν στη σελίδα, για διάγνωση."""
        out = BASE_DIR / "debug"
        out.mkdir(exist_ok=True)
        try:
            page.screenshot(path=str(out / f"{tag}.png"))
            self._collect_labels(page)
            labels = page.url + "\n" + "\n".join(getattr(self, "_seen_labels", {}) or page.evaluate(LABELS_JS))
            (out / f"{tag}.txt").write_text(labels, encoding="utf-8")
            print(f"[debug] αποθηκεύτηκαν {out / (tag + '.png')} και {tag}.txt", flush=True)
        except PWError as exc:
            print(f"[debug] αποτυχία: {exc}", flush=True)

    def _tile_selectors(self, ch: dict) -> list[str]:
        """Selectors για το 'πλακίδιο' του καναλιού στη λίστα καναλιών."""
        if ch.get("selector"):
            sel = ch["selector"]
            return sel if isinstance(sel, list) else [sel]
        m = ch.get("match", ch["name"]).replace('"', '\\"')
        return [
            # Magenta TV GR: <div class="channels-channel" aria-label="51 από 104 Novasports1HD">
            f'.channels-channel[aria-label$=" {m}" i]',
            f'[title="{m}" i]', f'img[alt="{m}" i]', f'[aria-label="{m}" i]',
            f'text="{m}"',
            f'[title*="{m}" i]', f'img[alt*="{m}" i]', f'[aria-label*="{m}" i]',
            f"text={m}",
        ] + [
            # Λογότυπα χωρίς κείμενο: ψάξε το όνομα μέσα στη διεύθυνση της εικόνας
            f'img[src*="{v}" i]' for v in self._compact_variants(m)
        ]

    @staticmethod
    def _compact_variants(name: str) -> list[str]:
        words = [w for w in "".join(c if c.isalnum() else " " for c in name).split() if w]
        out = []
        for sep in ("", "_", "-", "%20"):
            v = sep.join(words).lower()
            if v and v not in out:
                out.append(v)
        return out

    def _open_from_list(self, page: Page, ch: dict, selectors: list[str]) -> None:
        """Βρίσκει το κανάλι στη σελίδα καναλιών (με scroll αν χρειάζεται) και το πατάει."""
        self._set("loading", f"Επιλογή καναλιού {ch['name']}…")
        self._seen_labels: dict[str, None] = {}
        page.wait_for_timeout(1500)  # να φορτώσουν τα λογότυπα
        for _ in range(40):
            loc = self._first_visible(page, selectors, 800)
            if loc:
                loc.scroll_into_view_if_needed()
                loc.click()
                page.wait_for_timeout(1500)
                return
            self._collect_labels(page)
            at_end = page.evaluate(SCROLL_JS)
            if at_end:
                page.wait_for_timeout(1000)
                if page.evaluate(SCROLL_JS):
                    if self._first_visible(page, selectors, 1000):
                        continue
                    break
        self._dump_debug(page, "channels")
        raise PlayerError(
            f"Δεν βρέθηκε το '{ch['name']}' στη λίστα καναλιών (στοιχεία στον φάκελο debug) – βάλε σωστό \"match\" ή \"selector\" στο config.json"
        )

    def _do_mute(self, muted: bool) -> bool:
        self._require_playing()
        return self._page.evaluate(
            "m => { const v=[...document.querySelectorAll('video')].find(v=>v.readyState>=2);"
            " if(!v) return null; v.muted=m; window.__mmSync&&__mmSync(); return v.muted; }",
            bool(muted),
        )

    def _do_volume(self, vol: int) -> int:
        self._require_playing()
        return self._page.evaluate(
            "x => { const v=[...document.querySelectorAll('video')].find(v=>v.readyState>=2);"
            " if(!v) return null; v.volume=x/100; v.muted=(x===0); window.__mmSync&&__mmSync();"
            " return Math.round(v.volume*100); }",
            vol,
        )

    def _do_fullscreen(self, _arg: Any) -> str:
        """Πλήρης οθόνη του παραθύρου Chrome μέσω CDP (δουλεύει και από το web UI)."""
        self._require_playing()
        cdp = self._ctx.new_cdp_session(self._page)
        try:
            win = cdp.send("Browser.getWindowForTarget")
            wid = win["windowId"]
            cur = cdp.send("Browser.getWindowBounds", {"windowId": wid})["bounds"]["windowState"]
            new = "normal" if cur == "fullscreen" else "fullscreen"
            if new == "fullscreen" and cur != "normal":
                cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": new}})
            return new
        finally:
            cdp.detach()

    def _do_stop(self, _arg: Any) -> None:
        self._close_browser()
        self._current = None
        self._channel_url = None
        self._set("idle", "Σταμάτησε", channel=None)

    def _require_playing(self) -> None:
        if not self._locked or self._page is None or self._page.is_closed():
            raise PlayerError("Δεν παίζει κανάλι αυτή τη στιγμή")

    # --------------------------------------------------------- watchdog ----
    def _watchdog(self) -> None:
        """Κάθε ~1s: αν η σελίδα ξεκλειδώθηκε ή έφυγε από το κανάλι, το διορθώνει."""
        if not self._locked:
            return
        page = self._page
        if page is None or page.is_closed():
            self._close_browser()
            self._set("idle", "Το παράθυρο έκλεισε", channel=None)
            return
        try:
            if self._channel_url and page.url != self._channel_url:
                self._set("loading", "Επιστροφή στο κανάλι…")
                page.goto(self._channel_url, wait_until="domcontentloaded")
                self._wait_for_video(page, self.cfg["timeouts_ms"]["video"])
                self._set("playing", f"Παίζει: {self._current['name']}")
            if not page.evaluate("!!document.getElementById('mm-shield')"):
                if self._video_playing(page):
                    self._lock_page()
        except PWError as exc:
            print(f"[watchdog] {exc}", flush=True)
