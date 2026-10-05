"""omni as a desktop application: the interface in a native window (WebView2), backed by the local server.

Same stack as the browser version, without the browser: the window is the system's own web view (Edge WebView2 on
Windows 10/11), a few MB instead of an Electron runtime. The server is started inside the process.

* One instance: a second launch brings the first one's window to the front instead of opening another.
* The window opens at once on a splash page; the server starts behind it.
* Size, position and maximised state are remembered.
* Closing the window while a job runs keeps omni working in the notification area (icon near the clock); a toast tells
  when the job is done. Closing it with nothing running quits.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PORT = 8770
log = logging.getLogger("omni.app")

SPLASH = """<!doctype html><meta charset="utf-8"><title>omni</title>
<body style="margin:0;height:100vh;display:grid;place-items:center;background:#0b0b10;color:#e8e8f0;font:16px system-ui">
<div style="text-align:center"><div style="font-size:42px;font-weight:700;letter-spacing:.04em">omni</div>
<div style="margin-top:14px;opacity:.6">Démarrage…</div>
<div style="margin:22px auto 0;width:140px;height:3px;background:#222;border-radius:3px;overflow:hidden">
<div style="width:40%;height:100%;background:#7c6cf0;animation:m 1.1s ease-in-out infinite alternate"></div></div></div>
<style>@keyframes m{from{margin-left:0}to{margin-left:60%}}</style>"""

FAILED = """<!doctype html><meta charset="utf-8"><title>omni</title>
<body style="margin:0;height:100vh;display:grid;place-items:center;background:#0b0b10;color:#e8e8f0;font:16px system-ui">
<div style="max-width:520px;text-align:center"><div style="font-size:30px;font-weight:700">omni n'a pas pu démarrer</div>
<p style="opacity:.7">{why}</p><p style="opacity:.5;font-size:13px">Détails dans le journal : {log}</p></div>"""

WEBVIEW_HELP = ("omni utilise Microsoft Edge WebView2 pour sa fenêtre, mais il n'est pas disponible sur ce PC.\n\n"
                "Installe-le (microsoft.com/edge/webview2) ; en attendant, omni s'ouvre dans ton navigateur.")


# ---------------------------------------------------------------------------------------------------- helpers
def _get(url: str, timeout: float = 1.5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def running_url(port: int = PORT) -> str | None:
    """URL of an omni of this version already serving on ``port``."""
    from .ui.api import VERSION
    try:
        if _get(f"http://127.0.0.1:{port}/api/system").get("version") == VERSION:
            return f"http://127.0.0.1:{port}"
    except Exception:  # noqa: BLE001 - nothing there, or something else
        pass
    return None


def free_port(preferred: int = PORT, tries: int = 30) -> int:
    """First port that can really be bound (a port reserved by Hyper-V/WinNAT looks free to a connect test)."""
    for p in range(preferred, preferred + tries):
        with socket.socket() as s:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
            return p
    raise SystemExit(f"no free port in {preferred}-{preferred + tries - 1}")


def _running_jobs(url: str) -> int:
    try:
        return sum(1 for j in _get(f"{url}/api/jobs") if j.get("phase") in ("running", "queued"))
    except Exception:  # noqa: BLE001 - unreachable server: nothing to protect
        return 0


def _message(text: str, title: str = "omni", yes_no: bool = False) -> bool:
    """Native message box (Windows); for ``yes_no`` True when the user answers Yes."""
    try:
        flags = (0x4 if yes_no else 0x0) | 0x30 | 0x40000          # MB_YESNO | MB_ICONWARNING | MB_TOPMOST
        return ctypes.windll.user32.MessageBoxW(0, text, title, flags) == 6
    except Exception:  # noqa: BLE001
        return True


def icon_path() -> str | None:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    for p in (base / "assets" / "omni.ico", base / "omni" / "assets" / "omni.ico", Path(__file__).parent / "assets" / "omni.ico"):
        if p.is_file():
            return str(p)
    return None


# ---------------------------------------------------------------------------------------------------- instance
_handles: list = []


def _first_instance() -> bool:
    """Named mutex: False when another omni window process already owns it."""
    try:
        k32 = ctypes.windll.kernel32
        _handles.append(k32.CreateMutexW(None, False, "Local\\omni-desktop-app"))
        return k32.GetLastError() != 183                           # ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001
        return True


def _run_file() -> Path:
    from .core.config import CONFIG
    return CONFIG.workspace / "run.json"


def _focus_running() -> bool:
    """Ask the omni that owns the mutex to show its window (it may still be starting: wait a little)."""
    for _ in range(40):
        try:
            info = json.loads(_run_file().read_text(encoding="utf-8"))
            req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/focus", data=b"", method="POST")
            with urllib.request.urlopen(req, timeout=2):
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------------------------------- window state
def _state_file() -> Path:
    from .core.config import CONFIG
    return CONFIG.workspace / "window.json"


def _load_state() -> dict:
    try:
        s = json.loads(_state_file().read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _usable(state: dict) -> dict:
    """The saved geometry if it still fits on a screen, else defaults (a monitor may have been unplugged)."""
    w, h = int(state.get("width", 1500)), int(state.get("height", 950))
    out = {"width": max(1000, w), "height": max(640, h), "x": None, "y": None, "maximized": bool(state.get("maximized"))}
    try:
        import webview
        x, y = state.get("x"), state.get("y")
        if x is not None and y is not None:
            for s in webview.screens:
                if s.x - 100 <= x < s.x + s.width - 100 and s.y - 20 <= y < s.y + s.height - 100:
                    out["x"], out["y"] = int(x), int(y)
                    break
        for s in webview.screens[:1]:                             # never taller than the primary screen
            out["height"] = min(out["height"], s.height - 60)
            out["width"] = min(out["width"], s.width)
    except Exception:  # noqa: BLE001
        pass
    return out


def _hwnd_of_this_process() -> int:
    """Top-level visible window of this process (the omni window), 0 when none."""
    found: list[int] = []
    user32 = ctypes.windll.user32
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid() and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):   # GW_OWNER
            buf = ctypes.create_unicode_buffer(8)
            if user32.GetWindowTextW(hwnd, buf, 8):
                found.append(hwnd)
        return True
    user32.EnumWindows(proto(cb), 0)
    return found[0] if found else 0


# ---------------------------------------------------------------------------------------------------- server
class Server:
    """The API + interface served by uvicorn in a thread of this process."""

    def __init__(self, port: int):
        import uvicorn

        from .ui.api import create_app
        self.port = port
        self.app = create_app()
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="warning",
                                                    log_config=None))
        self.thread = threading.Thread(target=self.server.run, daemon=True, name="uvicorn")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 30.0) -> "Server":
        self.thread.start()
        t0 = time.time()
        while not self.server.started:
            if not self.thread.is_alive():
                raise RuntimeError("le serveur local n'a pas démarré")
            if time.time() - t0 > timeout:
                raise RuntimeError("le serveur local met trop de temps à démarrer")
            time.sleep(0.05)
        try:
            _run_file().write_text(json.dumps({"pid": os.getpid(), "port": self.port}), encoding="utf-8")
        except OSError:
            pass
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)
        try:
            _run_file().unlink(missing_ok=True)
        except OSError:
            pass


def _serve_forever(server: Server | None, target: str) -> None:
    webbrowser.open(target)
    if server is None:
        return
    print(f"omni : {server.url}  (Ctrl+C ou « Arrêter omni » dans les réglages pour quitter)")
    try:
        while server.thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    server.stop()


# ---------------------------------------------------------------------------------------------------- run
def run(port: int = PORT, browser: bool = False, path: str = "/") -> int:
    """Open omni in its own window (or in the default browser with ``browser=True`` / when no web view is available)."""
    from .core import log as logs
    from .core import settings, windows
    from .core.config import CONFIG
    settings.apply()
    if not browser and not _first_instance():
        _focus_running()
        return 0

    url = running_url(port)
    server: Server | None = None
    if browser:
        if url is None:
            server = Server(free_port(port)).start()
            url = server.url
        _serve_forever(server, url + path)
        return 0

    try:
        import webview
    except Exception as e:  # noqa: BLE001
        log.error("fenêtre intégrée indisponible: %s", e)
        server = None if url else Server(free_port(port)).start()
        _serve_forever(server, (url or server.url) + path)           # type: ignore[union-attr]
        return 0

    geo = _usable(_load_state())
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window("omni", html=SPLASH, width=geo["width"], height=geo["height"], x=geo["x"], y=geo["y"],
                                   min_size=(1000, 640), background_color="#0b0b10", text_select=True)
    box: dict = {"url": url, "server": None, "quitting": False, "hwnd": 0}

    def pick(title: str) -> str:
        res = window.create_file_dialog(webview.FileDialog.FOLDER, allow_multiple=False)
        return res[0] if res else ""
    windows.set_picker(pick)

    from .tray import Tray

    def show() -> None:
        try:
            window.show()
            window.restore()
            hwnd = box["hwnd"] or _hwnd_of_this_process()
            if hwnd:
                box["hwnd"] = hwnd
                ctypes.windll.user32.ShowWindow(hwnd, 9)              # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001
            log.debug("focus failed", exc_info=True)

    def quit_all() -> None:
        box["quitting"] = True
        window.destroy()

    tray = Tray(show, quit_all, icon_path())

    def in_front() -> bool:
        hwnd = box["hwnd"]
        return bool(hwnd) and ctypes.windll.user32.GetForegroundWindow() == hwnd and bool(ctypes.windll.user32.IsWindowVisible(hwnd))

    def job_finished(job: dict) -> None:
        """A long job ended while the user is elsewhere (window hidden, minimised or another app in front)."""
        if in_front() or (job.get("ended") or 0) - (job.get("started") or 0) < 15:
            return
        ok = job["phase"] == "done"
        failed = (job.get("counts") or {}).get("FAILED", 0)
        tray.notify(f"{job['label']} : {'terminé' if ok else 'interrompu' if job['phase'] == 'interrupted' else job['phase']}",
                    f"{failed} échec(s). Ouvre omni pour le détail." if failed else "Ouvre omni pour voir le résultat.")

    def closing():
        """Closing with a job running hides the window and lets omni finish in the background."""
        if box["quitting"]:
            return True
        _save_geometry()
        n = _running_jobs(box["url"])
        if not n:
            return True
        if tray.start():
            window.hide()
            tray.notify("omni continue en arrière-plan", f"{n} travail(aux) en cours. Clique sur l'icône pour rouvrir omni.")
            return False
        return _message(f"{n} travail(aux) en cours seront interrompus.\n\nFermer omni quand même ?", yes_no=True)

    def _save_geometry() -> None:
        try:
            hwnd = box["hwnd"] or _hwnd_of_this_process()
            maximized = bool(hwnd and ctypes.windll.user32.IsZoomed(hwnd))
            old = _load_state()
            state = {"maximized": maximized}
            if not maximized:                                          # keep the normal size when closing maximised
                state.update(x=window.x, y=window.y, width=window.width, height=window.height)
            else:
                state.update({k: old[k] for k in ("x", "y", "width", "height") if k in old})
            _state_file().write_text(json.dumps(state), encoding="utf-8")
        except Exception:  # noqa: BLE001
            log.debug("could not save the window state", exc_info=True)

    window.events.closing += closing

    def boot() -> None:
        """Behind the splash: start the server, then load the interface."""
        try:
            if box["url"] is None:
                srv = Server(free_port(port)).start()
                box["server"], box["url"] = srv, srv.url
                srv.app.state.shell.focus = show
                srv.app.state.shell.job_finished = job_finished
            window.load_url(box["url"] + path)
            box["hwnd"] = _hwnd_of_this_process()
            if geo["maximized"]:
                window.maximize()
        except Exception as e:  # noqa: BLE001
            log.exception("startup failed")
            window.load_html(FAILED.format(why=str(e).replace("<", "&lt;"), log=logs.log_dir() / "omni.log"))

    storage = CONFIG.workspace / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    try:
        webview.start(boot, private_mode=False, storage_path=str(storage), icon=icon_path())
    except Exception as e:  # noqa: BLE001 - no WebView2 runtime: the browser still works
        windows.set_picker(None)                 # the dialog it registered belongs to a window that never opened
        log.error("fenêtre intégrée impossible: %s", e, exc_info=True)
        _message(WEBVIEW_HELP)
        if box["server"] is None and box["url"] is None:
            box["server"] = Server(free_port(port)).start()
            box["url"] = box["server"].url
        _serve_forever(box["server"], box["url"] + path)
        tray.stop()
        return 0
    tray.stop()
    if box["server"] is not None:
        box["server"].stop()
    return 0
