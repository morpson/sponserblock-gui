#!/usr/bin/env python3
"""Combined GUI for iSponsorBlockTV.

One window that:
  - Starts / stops the background service
  - Shows a live log tail
  - Pairs with a YouTube TV device (DIAL scan or manual screen ID)
  - Searches YouTube and plays videos
  - Controls playback and volume
"""
import asyncio
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENV_PYTHON  = sys.executable
SRC_DIR      = os.path.join(PROJECT_DIR, "src")
if sys.platform == "win32":
    DATA_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "iSponsorBlockTV"
    )
else:
    DATA_DIR = os.path.join(PROJECT_DIR, "data")
PID_FILE     = os.path.join(DATA_DIR, "service.pid")
LOG_FILE     = os.path.join(DATA_DIR, "service.log")
GUI_STATE    = os.path.join(DATA_DIR, "gui_state.json")

# Make sure the project source is importable when run directly
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
def _detect_dark() -> bool:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except (ImportError, OSError):
            return False
    try:
        r = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True, text=True,
        )
        return r.stdout.strip().lower() == "dark"
    except Exception:
        return False

DARK = {
    "bg": "#1e1e1e", "panel": "#2a2a2a", "panel2": "#242424",
    "border": "#3a3a3a", "text": "#f0f0f0", "subtext": "#888888",
    "accent": "#0a84ff", "accent_fg": "#ffffff",
    "danger": "#ff453a", "danger_fg": "#ffffff",
    "success": "#30d158", "warn": "#ffd60a",
    "log_bg": "#141414", "log_fg": "#cccccc",
    "entry_bg": "#333333", "entry_fg": "#f0f0f0",
    "list_bg": "#1a1a1a", "list_sel": "#0a84ff", "list_sel_fg": "#ffffff",
    "btn_disabled_bg": "#333333", "btn_disabled_fg": "#555555",
}
LIGHT = {
    "bg": "#f2f2f7", "panel": "#ffffff", "panel2": "#f9f9f9",
    "border": "#d1d1d6", "text": "#1c1c1e", "subtext": "#8e8e93",
    "accent": "#007aff", "accent_fg": "#ffffff",
    "danger": "#ff3b30", "danger_fg": "#ffffff",
    "success": "#34c759", "warn": "#ff9f0a",
    "log_bg": "#f9f9f9", "log_fg": "#2c2c2e",
    "entry_bg": "#ffffff", "entry_fg": "#1c1c1e",
    "list_bg": "#ffffff", "list_sel": "#007aff", "list_sel_fg": "#ffffff",
    "btn_disabled_bg": "#e5e5ea", "btn_disabled_fg": "#aeaeb2",
}
C = DARK if _detect_dark() else LIGHT

# ---------------------------------------------------------------------------
# PID / service helpers
# ---------------------------------------------------------------------------

def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

def _read_pid() -> "int | None":
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None

def _write_pid(pid: int) -> None:
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(pid))

def _remove_pid() -> None:
    try:
        os.remove(PID_FILE)
    except Exception:
        pass

def service_running() -> bool:
    pid = _read_pid()
    return bool(pid and _is_running(pid))

# ---------------------------------------------------------------------------
# GUI state (saved paired devices)
# ---------------------------------------------------------------------------

def _load_gui_state() -> dict:
    try:
        with open(GUI_STATE, "r", encoding="utf-8") as f:
            import json as _json
            return _json.load(f)
    except Exception:
        return {}

def _save_gui_state(state: dict) -> None:
    import json as _json
    os.makedirs(os.path.dirname(GUI_STATE), exist_ok=True)
    with open(GUI_STATE, "w", encoding="utf-8") as f:
        _json.dump(state, f, indent=2)

def _save_paired_device(screen_id: str, name: str) -> None:
    """Save device to gui_state, moving it to front (most recent)."""
    state = _load_gui_state()
    devices = state.get("paired_devices", [])
    # Remove any existing entry for this screen_id
    devices = [d for d in devices if d["screen_id"] != screen_id]
    # Insert at front
    devices.insert(0, {"screen_id": screen_id, "name": name})
    # Keep max 10
    state["paired_devices"] = devices[:10]
    state["last_screen_id"] = screen_id
    state["last_name"] = name
    _save_gui_state(state)

# ---------------------------------------------------------------------------
# Async bridge
# ---------------------------------------------------------------------------
# All async work (aiohttp, ytlounge) runs in a dedicated daemon thread that
# owns the asyncio event loop.  tkinter callbacks post coroutines via
# _async_queue and receive results back via root.after() on the main thread.

_async_loop: asyncio.AbstractEventLoop | None = None
_async_queue: queue.Queue = queue.Queue()

def _async_thread_main():
    global _async_loop
    _async_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_async_loop)
    _async_loop.run_until_complete(_async_worker())

async def _async_worker():
    while True:
        try:
            coro, callback = _async_queue.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.05)
            continue
        try:
            result = await coro
            if callback:
                callback(result, None)
        except Exception as exc:
            if callback:
                callback(None, exc)

def run_async(coro, callback=None):
    """Submit a coroutine to the async thread. callback(result, error) is
    called on the async thread — callers must use root.after() if they need
    to touch tkinter widgets."""
    _async_queue.put((coro, callback))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lighten(hex_color: str, factor: float = 1.15) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4))
    return "#{:02x}{:02x}{:02x}".format(
        min(255, int(r * factor)),
        min(255, int(g * factor)),
        min(255, int(b * factor)),
    )

def _make_btn(parent, text, bg, fg, cmd, width=None):
    """Flat, hover-highlighted label-button."""
    kw = dict(text=text, bg=bg, fg=fg, padx=14, pady=7,
              cursor="hand2", relief=tk.FLAT, anchor="center")
    if width:
        kw["width"] = width
    btn = tk.Label(parent, **kw)
    btn.bind("<Button-1>", lambda e: cmd())
    btn.bind("<Enter>", lambda e, b=btn, o=bg: b.configure(bg=_lighten(o)))
    btn.bind("<Leave>", lambda e, b=btn, o=bg: b.configure(bg=o))
    return btn

def _section_label(parent, text):
    return tk.Label(parent, text=text, bg=C["bg"], fg=C["subtext"],
                    font=("SF Pro Text", 10), anchor="w")

def _divider(parent):
    return tk.Frame(parent, bg=C["border"], height=1)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class App(tk.Tk):

    POLL_MS   = 1500
    LOG_LINES = 300

    def __init__(self):
        super().__init__()
        self.title("iSponsorBlockTV")
        self.configure(bg=C["bg"])
        self.minsize(620, 680)
        self.resizable(True, True)

        # Async state (owned/touched only from async thread, except reads)
        self._web_session  = None
        self._api_helper   = None
        self._lounge       = None
        self._scan_results = []   # list of {screen_id, name}
        self._search_results = [] # list of (video_id, title)

        # Log tail state
        self._log_pos  = 0
        self._log_stop = threading.Event()

        self._build_fonts()
        self._build_ui()
        self._init_async_state()
        self._start_log_tail()
        self._poll()

    # ------------------------------------------------------------------
    # Fonts
    # ------------------------------------------------------------------
    def _build_fonts(self):
        self.f_title  = tkfont.Font(family="SF Pro Display", size=15, weight="bold")
        self.f_body   = tkfont.Font(family="SF Pro Text",    size=12)
        self.f_small  = tkfont.Font(family="SF Pro Text",    size=11)
        self.f_mono   = tkfont.Font(family="SF Mono",        size=11)
        self.f_btn    = tkfont.Font(family="SF Pro Text",    size=12, weight="bold")
        self.f_sec    = tkfont.Font(family="SF Pro Text",    size=10)

    # ------------------------------------------------------------------
    # Layout skeleton
    # ------------------------------------------------------------------
    def _build_ui(self):
        # ── Top header bar ───────────────────────────────────────────
        hdr = tk.Frame(self, bg=C["bg"], padx=18, pady=14)
        hdr.pack(fill=tk.X)

        tk.Label(hdr, text="iSponsorBlockTV", bg=C["bg"],
                 fg=C["text"], font=self.f_title).pack(side=tk.LEFT)

        # Service status badge (right)
        badge = tk.Frame(hdr, bg=C["bg"])
        badge.pack(side=tk.RIGHT)
        self._svc_dot = tk.Label(badge, text="●", bg=C["bg"],
                                 fg=C["subtext"], font=self.f_body)
        self._svc_dot.pack(side=tk.LEFT)
        self._svc_label = tk.Label(badge, text="Checking…", bg=C["bg"],
                                   fg=C["subtext"], font=self.f_small)
        self._svc_label.pack(side=tk.LEFT, padx=(4, 0))

        _divider(self).pack(fill=tk.X)

        # ── Two-column body ──────────────────────────────────────────
        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        body.columnconfigure(0, weight=1, minsize=280)
        body.columnconfigure(1, weight=1, minsize=280)
        body.rowconfigure(0, weight=1)

        left  = tk.Frame(body, bg=C["bg"])
        right = tk.Frame(body, bg=C["bg"])
        left.grid( row=0, column=0, sticky="nsew", padx=(0, 6))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._build_service_section(left)
        self._build_pairing_section(left)
        self._build_playback_section(right)
        self._build_search_section(right)
        self._build_log_section(self)   # full-width at bottom

    # ------------------------------------------------------------------
    # Service section (start / stop)
    # ------------------------------------------------------------------
    def _build_service_section(self, parent):
        box = tk.Frame(parent, bg=C["panel"], padx=14, pady=12)
        box.pack(fill=tk.X, pady=(0, 10))

        _section_label(box, "SERVICE").pack(anchor="w", pady=(0, 8))

        row = tk.Frame(box, bg=C["panel"])
        row.pack()

        self._start_btn = _make_btn(row, "▶  Start",  C["accent"],  C["accent_fg"],  self._svc_start, width=10)
        self._start_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._stop_btn  = _make_btn(row, "■  Stop",   C["danger"],  C["danger_fg"],  self._svc_stop,  width=10)
        self._stop_btn.pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Device Pairing section
    # ------------------------------------------------------------------
    def _build_pairing_section(self, parent):
        box = tk.Frame(parent, bg=C["panel"], padx=14, pady=12)
        box.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Header row
        hdr = tk.Frame(box, bg=C["panel"])
        hdr.pack(fill=tk.X, pady=(0, 8))
        _section_label(hdr, "DEVICE PAIRING").pack(side=tk.LEFT)
        self._scan_btn = _make_btn(hdr, "Scan", C["accent"], C["accent_fg"], self._do_scan)
        self._scan_btn.pack(side=tk.RIGHT)

        # Connected device indicator
        self._paired_label = tk.Label(
            box, text="● Not paired", bg=C["panel"],
            fg=C["subtext"], font=self.f_small, anchor="w",
        )
        self._paired_label.pack(fill=tk.X, pady=(0, 6))

        # Device list
        list_frame = tk.Frame(box, bg=C["border"], padx=1, pady=1)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self._device_lb = tk.Listbox(
            list_frame, bg=C["list_bg"], fg=C["text"],
            selectbackground=C["list_sel"], selectforeground=C["list_sel_fg"],
            relief=tk.FLAT, bd=0, font=self.f_small,
            height=4, activestyle="none",
        )
        self._device_lb.pack(fill=tk.BOTH, expand=True)
        self._device_lb.bind("<<ListboxSelect>>", self._on_device_select)

        # Manual entry
        manual = tk.Frame(box, bg=C["panel"])
        manual.pack(fill=tk.X)
        self._screen_id_var = tk.StringVar()
        entry = tk.Entry(manual, textvariable=self._screen_id_var,
                         bg=C["entry_bg"], fg=C["entry_fg"],
                         insertbackground=C["text"], relief=tk.FLAT,
                         font=self.f_small)
        entry.insert(0, "Screen ID")
        entry.bind("<FocusIn>",  lambda e: entry.delete(0, tk.END) if entry.get() == "Screen ID" else None)
        entry.bind("<FocusOut>", lambda e: entry.insert(0, "Screen ID") if not entry.get() else None)
        entry.bind("<Return>", lambda e: self._do_manual_pair())
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 6))

        _make_btn(manual, "Pair", C["accent"], C["accent_fg"],
                  self._do_manual_pair).pack(side=tk.LEFT)
        self._screen_id_entry = entry

    # ------------------------------------------------------------------
    # Playback section
    # ------------------------------------------------------------------
    def _build_playback_section(self, parent):
        box = tk.Frame(parent, bg=C["panel"], padx=14, pady=12)
        box.pack(fill=tk.X, pady=(0, 10))

        _section_label(box, "PLAYBACK").pack(anchor="w", pady=(0, 8))

        pb_row = tk.Frame(box, bg=C["panel"])
        pb_row.pack(pady=(0, 6))
        for label, cmd in [
            ("⏮ -10s", self._do_rewind),
            ("▶ Play",  self._do_play),
            ("⏸ Pause", self._do_pause),
            ("⏭ +10s",  self._do_ff),
        ]:
            _make_btn(pb_row, label, C["panel2"], C["text"], cmd).pack(side=tk.LEFT, padx=3)

        vol_row = tk.Frame(box, bg=C["panel"])
        vol_row.pack()
        _make_btn(vol_row, "🔉 Vol −", C["panel2"], C["text"], self._do_vol_down).pack(side=tk.LEFT, padx=3)
        _make_btn(vol_row, "🔊 Vol +", C["panel2"], C["text"], self._do_vol_up).pack(side=tk.LEFT, padx=3)

    # ------------------------------------------------------------------
    # Search section
    # ------------------------------------------------------------------
    def _build_search_section(self, parent):
        box = tk.Frame(parent, bg=C["panel"], padx=14, pady=12)
        box.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        _section_label(box, "SEARCH").pack(anchor="w", pady=(0, 8))

        search_row = tk.Frame(box, bg=C["panel"])
        search_row.pack(fill=tk.X, pady=(0, 8))

        self._search_var = tk.StringVar()
        search_entry = tk.Entry(
            search_row, textvariable=self._search_var,
            bg=C["entry_bg"], fg=C["entry_fg"],
            insertbackground=C["text"], relief=tk.FLAT, font=self.f_small,
        )
        search_entry.insert(0, "Search YouTube…")
        search_entry.bind("<FocusIn>",  lambda e: search_entry.delete(0, tk.END) if search_entry.get() == "Search YouTube…" else None)
        search_entry.bind("<FocusOut>", lambda e: search_entry.insert(0, "Search YouTube…") if not search_entry.get() else None)
        search_entry.bind("<Return>", lambda e: self._do_search())
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 6))
        _make_btn(search_row, "Search", C["accent"], C["accent_fg"], self._do_search).pack(side=tk.LEFT)
        self._search_entry = search_entry

        list_frame = tk.Frame(box, bg=C["border"], padx=1, pady=1)
        list_frame.pack(fill=tk.BOTH, expand=True)
        sb = tk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self._results_lb = tk.Listbox(
            list_frame, bg=C["list_bg"], fg=C["text"],
            selectbackground=C["list_sel"], selectforeground=C["list_sel_fg"],
            relief=tk.FLAT, bd=0, font=self.f_small,
            yscrollcommand=sb.set, activestyle="none",
        )
        sb.config(command=self._results_lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._results_lb.pack(fill=tk.BOTH, expand=True)
        self._results_lb.bind("<<ListboxSelect>>", self._on_result_select)

    # ------------------------------------------------------------------
    # Log section
    # ------------------------------------------------------------------
    def _build_log_section(self, parent):
        _divider(parent).pack(fill=tk.X, padx=12)

        log_hdr = tk.Frame(parent, bg=C["bg"], padx=14, pady=6)
        log_hdr.pack(fill=tk.X)
        tk.Label(log_hdr, text="LIVE LOG", bg=C["bg"],
                 fg=C["subtext"], font=self.f_sec).pack(side=tk.LEFT)
        tk.Button(log_hdr, text="Clear", bg=C["bg"], fg=C["subtext"],
                  activebackground=C["bg"], activeforeground=C["text"],
                  relief=tk.FLAT, bd=0, font=self.f_sec,
                  cursor="hand2", command=self._clear_log).pack(side=tk.RIGHT)

        log_frame = tk.Frame(parent, bg=C["border"], padx=1, pady=1)
        log_frame.pack(fill=tk.X, padx=12, pady=(0, 12))

        vsb = tk.Scrollbar(log_frame, orient=tk.VERTICAL)
        hsb = tk.Scrollbar(log_frame, orient=tk.HORIZONTAL)
        self._log_text = tk.Text(
            log_frame, bg=C["log_bg"], fg=C["log_fg"],
            font=self.f_mono, relief=tk.FLAT, bd=0,
            wrap=tk.NONE, height=7, state=tk.DISABLED,
            selectbackground=C["accent"],
            yscrollcommand=vsb.set, xscrollcommand=hsb.set,
        )
        vsb.config(command=self._log_text.yview)
        hsb.config(command=self._log_text.xview)
        vsb.pack(side=tk.RIGHT,  fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._log_text.pack(fill=tk.X)

    # ------------------------------------------------------------------
    # Async init (called once on startup to create session + api_helper)
    # ------------------------------------------------------------------
    def _init_async_state(self):
        def _done(result, err):
            if err:
                self.after(0, self._append_log, f"[ERROR] init: {err}\n")
            else:
                self._web_session, self._api_helper = result
                self.after(0, self._on_init_complete)
        run_async(self._async_init(), _done)

    def _on_init_complete(self):
        """Called on the main thread once session + api_helper are ready."""
        state = _load_gui_state()
        saved = state.get("paired_devices", [])

        # Fall back to devices in config.json if gui_state has nothing
        if not saved and hasattr(self, "_config") and self._config.devices:
            import json as _json
            saved = [
                {"screen_id": d.get("screen_id", ""), "name": d.get("name", "Unknown")}
                for d in self._config.devices
                if d.get("screen_id")
            ]
            # Seed gui_state so they show up next time too
            if saved:
                state["paired_devices"] = saved
                state["last_screen_id"] = saved[0]["screen_id"]
                state["last_name"] = saved[0]["name"]
                _save_gui_state(state)

        # Populate the device listbox with saved devices
        if saved:
            self._device_lb.delete(0, tk.END)
            self._scan_results = saved
            for d in saved:
                self._device_lb.insert(tk.END, f"★ {d['name']}")

        # Auto-connect to the last used device
        last_id   = state.get("last_screen_id")
        last_name = state.get("last_name", last_id)
        if last_id:
            self._append_log(f"[INFO] Auto-connecting to last device: {last_name}\n")
            self._pair_with(last_id, last_name, save=False)

    async def _async_init(self):
        import aiohttp
        from iSponsorBlockTV import api_helpers, helpers
        config = helpers.Config(DATA_DIR)
        session = aiohttp.ClientSession(trust_env=config.use_proxy)
        helper  = api_helpers.ApiHelper(config, session)
        # stash config for later use
        self._config = config
        return session, helper

    # ------------------------------------------------------------------
    # Service handlers
    # ------------------------------------------------------------------
    def _svc_start(self):
        if service_running():
            return
        cmd = [VENV_PYTHON, "-m", "iSponsorBlockTV", "--data", DATA_DIR, "start"]
        try:
            log_fd = open(LOG_FILE, "a", encoding="utf-8")
            proc = subprocess.Popen(
                cmd, cwd=PROJECT_DIR,
                stdout=log_fd, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception as exc:
            self._append_log(f"[ERROR] Failed to start: {exc}\n")
            return
        _write_pid(proc.pid)
        self._refresh_status()

    def _svc_stop(self):
        pid = _read_pid()
        if not pid:
            self._refresh_status()
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            _remove_pid()
            self._refresh_status()
            return
        except Exception as exc:
            self._append_log(f"[ERROR] {exc}\n")
            return
        for _ in range(20):
            if not _is_running(pid):
                break
            time.sleep(0.1)
        if _is_running(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
        _remove_pid()
        self._refresh_status()

    def _refresh_status(self):
        running = service_running()
        if running:
            pid = _read_pid()
            self._svc_dot.configure(fg=C["success"])
            self._svc_label.configure(fg=C["success"], text=f"Running  ·  PID {pid}")
            self._start_btn.configure(bg=C["btn_disabled_bg"], fg=C["btn_disabled_fg"], cursor="arrow")
            self._stop_btn.configure( bg=C["danger"],          fg=C["danger_fg"],        cursor="hand2")
        else:
            self._svc_dot.configure(fg=C["subtext"])
            self._svc_label.configure(fg=C["subtext"], text="Stopped")
            self._start_btn.configure(bg=C["accent"],          fg=C["accent_fg"],        cursor="hand2")
            self._stop_btn.configure( bg=C["btn_disabled_bg"], fg=C["btn_disabled_fg"],  cursor="arrow")

    def _poll(self):
        self._refresh_status()
        self.after(self.POLL_MS, self._poll)

    # ------------------------------------------------------------------
    # Device pairing handlers
    # ------------------------------------------------------------------
    def _do_scan(self):
        if not self._api_helper:
            self._append_log("[WARN] Still initialising, try again in a moment.\n")
            return
        self._scan_btn.configure(text="Scanning…", cursor="arrow")

        def _done(devices, err):
            def _update():
                self._scan_btn.configure(text="Scan", cursor="hand2")
                if err:
                    self._append_log(f"[ERROR] Scan failed: {err}\n")
                    return
                # Merge scan results with saved devices (scan results first, deduped)
                saved = _load_gui_state().get("paired_devices", [])
                seen = set()
                merged = []
                for d in (devices or []):
                    if d["screen_id"] not in seen:
                        merged.append(d)
                        seen.add(d["screen_id"])
                for d in saved:
                    if d["screen_id"] not in seen:
                        merged.append(d)
                        seen.add(d["screen_id"])
                self._scan_results = merged
                self._device_lb.delete(0, tk.END)
                for d in merged:
                    prefix = "●" if d in (devices or []) else "★"
                    self._device_lb.insert(tk.END, f"{prefix} {d['name']}")
                if not merged:
                    self._device_lb.insert(tk.END, "No devices found")
            self.after(0, _update)

        run_async(self._api_helper.discover_youtube_devices_dial(), _done)

    def _on_device_select(self, event):
        sel = self._device_lb.curselection()
        if not sel or not self._scan_results:
            return
        idx = sel[0]
        if idx >= len(self._scan_results):
            return
        device = self._scan_results[idx]
        self._pair_with(device["screen_id"], device["name"])

    def _do_manual_pair(self):
        screen_id = self._screen_id_var.get().strip()
        if not screen_id or screen_id == "Screen ID":
            return
        self._pair_with(screen_id, screen_id + " (manual)")
        self._screen_id_entry.delete(0, tk.END)

    def _pair_with(self, screen_id: str, display_name: str, save: bool = True):
        if not self._api_helper:
            self._append_log("[WARN] Still initialising, try again in a moment.\n")
            return

        def _done(lounge, err):
            def _update():
                if err:
                    self._paired_label.configure(
                        text=f"● Error pairing: {err}", fg=C["danger"])
                    return
                self._lounge = lounge
                self._paired_label.configure(
                    text=f"● {display_name}", fg=C["success"])
                if save:
                    _save_paired_device(screen_id, display_name)
                    self._refresh_saved_list()
            self.after(0, _update)

        run_async(self._async_pair(screen_id), _done)

    def _refresh_saved_list(self):
        """Repopulate device listbox from saved state."""
        state = _load_gui_state()
        saved = state.get("paired_devices", [])
        self._device_lb.delete(0, tk.END)
        self._scan_results = saved
        for d in saved:
            self._device_lb.insert(tk.END, f"★ {d['name']}")

    async def _async_pair(self, screen_id: str):
        from iSponsorBlockTV import ytlounge
        logger = logging.getLogger("gui")
        lounge = ytlounge.YtLoungeApi(
            screen_id, self._config, self._api_helper, logger)
        await lounge.change_web_session(self._web_session)
        return lounge

    # ------------------------------------------------------------------
    # Playback handlers
    # ------------------------------------------------------------------
    def _do_play(self):
        if self._lounge:
            run_async(self._lounge._command("play"))

    def _do_pause(self):
        if self._lounge:
            run_async(self._lounge._command("pause"))

    def _do_rewind(self):
        if self._lounge:
            run_async(self._async_seek(-10))

    def _do_ff(self):
        if self._lounge:
            run_async(self._async_seek(10))

    async def _async_seek(self, delta: int):
        state = await self._lounge.get_now_playing() or {}
        new_time = max(0, int(float(state.get("currentTime", 0))) + delta)
        await self._lounge._command("seekTo", {"newTime": str(new_time)})

    def _do_vol_up(self):
        if self._lounge:
            run_async(self._async_set_volume(10))

    def _do_vol_down(self):
        if self._lounge:
            run_async(self._async_set_volume(-10))

    async def _async_set_volume(self, delta: int):
        current = int(self._lounge.volume_state.get("volume", 50))
        await self._lounge.set_volume(max(0, min(100, current + delta)))

    # ------------------------------------------------------------------
    # Search handlers
    # ------------------------------------------------------------------
    def _do_search(self):
        query = self._search_var.get().strip()
        if not query or query == "Search YouTube…":
            return
        if not self._web_session:
            self._append_log("[WARN] Still initialising.\n")
            return

        self._results_lb.delete(0, tk.END)
        self._results_lb.insert(tk.END, "Searching…")

        def _done(results, err):
            def _update():
                self._results_lb.delete(0, tk.END)
                if err:
                    self._results_lb.insert(tk.END, f"Error: {err}")
                    return
                self._search_results = results or []
                if self._search_results:
                    for _, title in self._search_results:
                        self._results_lb.insert(tk.END, title)
                else:
                    self._results_lb.insert(tk.END, "No results")
            self.after(0, _update)

        run_async(self._async_search(query), _done)

    async def _async_search(self, query: str):
        """Search YouTube via the public innertube API — no API key required."""
        import json as _json
        url = "https://www.youtube.com/youtubei/v1/search"
        payload = {
            "query": query,
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240101",
                }
            },
        }
        headers = {"Content-Type": "application/json"}
        async with self._web_session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json(content_type=None)

        results = []
        try:
            items = (
                data["contents"]["twoColumnSearchResultsRenderer"]
                   ["primaryContents"]["sectionListRenderer"]["contents"]
            )
            for section in items:
                for item in section.get("itemSectionRenderer", {}).get("contents", []):
                    vr = item.get("videoRenderer")
                    if not vr:
                        continue
                    video_id = vr.get("videoId")
                    title_runs = vr.get("title", {}).get("runs", [])
                    title = "".join(r.get("text", "") for r in title_runs)
                    if video_id and title:
                        results.append((video_id, title))
                    if len(results) >= 10:
                        break
                if len(results) >= 10:
                    break
        except (KeyError, TypeError):
            pass
        return results

    def _on_result_select(self, event):
        sel = self._results_lb.curselection()
        if not sel or not self._search_results or not self._lounge:
            return
        idx = sel[0]
        if idx >= len(self._search_results):
            return
        video_id, title = self._search_results[idx]
        self._append_log(f"[PLAY] {title}\n")
        run_async(self._lounge.play_video(video_id))

    # ------------------------------------------------------------------
    # Log tail
    # ------------------------------------------------------------------
    def _start_log_tail(self):
        try:
            self._log_pos = os.path.getsize(LOG_FILE)
        except OSError:
            self._log_pos = 0
        self._log_stop.clear()
        t = threading.Thread(target=self._tail_worker, daemon=True)
        t.start()

    def _tail_worker(self):
        while not self._log_stop.is_set():
            try:
                size = os.path.getsize(LOG_FILE)
            except OSError:
                time.sleep(0.5)
                continue
            if size < self._log_pos:
                self._log_pos = 0
            if size > self._log_pos:
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(self._log_pos)
                    data = f.read()
                    self._log_pos = f.tell()
                if data:
                    self.after(0, self._append_log, data)
            time.sleep(0.4)

    def _append_log(self, text: str):
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.insert(tk.END, text)
        lines = int(self._log_text.index(tk.END).split(".")[0])
        if lines > self.LOG_LINES:
            self._log_text.delete("1.0", f"{lines - self.LOG_LINES}.0")
        self._log_text.see(tk.END)
        self._log_text.configure(state=tk.DISABLED)

    def _clear_log(self):
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.delete("1.0", tk.END)
        self._log_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------
    def _on_close(self):
        self._log_stop.set()
        # Close aiohttp session gracefully
        if self._web_session:
            run_async(self._web_session.close())
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Start the async worker thread
    _t = threading.Thread(target=_async_thread_main, daemon=True)
    _t.start()

    app = App()
    app.protocol("WM_DELETE_WINDOW", app._on_close)
    app.mainloop()
