"""omni as a desktop application: the interface in a native window (WebView2), backed by the local server.

Same stack as the browser version, without the browser: the window is the system's own web view (Edge WebView2 on
Windows 10/11), a few MB instead of an Electron runtime. The server is started inside the process; when an omni of the
same version is already running, the window simply opens on it. Closing the window stops the server it started.
"""
from __future__ import annotations

import json
import logging
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PORT = 8770
log = logging.getLogger("omni.app")


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
        return sum(1 for j in _get(f"{url}/api/jobs") if j.get("phase") == "running")
    except Exception:  # noqa: BLE001 - unreachable server: nothing to protect
        return 0


def _confirm(text: str, title: str = "omni") -> bool:
    """Native yes/no box (Windows); True when the user agrees."""
    try:
        import ctypes
        MB_YESNO, MB_ICONWARNING, MB_TOPMOST = 0x4, 0x30, 0x40000
        return ctypes.windll.user32.MessageBoxW(0, text, title, MB_YESNO | MB_ICONWARNING | MB_TOPMOST) == 6
    except Exception:  # noqa: BLE001
        return True


class Server:
    """The API + interface served by uvicorn in a thread of this process."""

    def __init__(self, port: int):
        import uvicorn

        from .ui.api import create_app
        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning",
                                                    log_config=None))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

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
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


def icon_path() -> str | None:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    for p in (base / "assets" / "omni.ico", base / "omni" / "assets" / "omni.ico", Path(__file__).parent / "assets" / "omni.ico"):
        if p.is_file():
            return str(p)
    return None


def run(port: int = PORT, browser: bool = False, path: str = "/") -> int:
    """Open omni in its own window (or in the default browser with ``browser=True`` / when no web view is available)."""
    from .core import settings, windows
    from .core.config import CONFIG
    settings.apply()
    url = running_url(port)
    server = None
    if url is None:
        server = Server(free_port(port)).start()
        url = server.url
    target = url + path

    def stop():
        if server is not None:
            server.stop()

    if not browser:
        try:
            import webview
        except Exception as e:  # noqa: BLE001
            log.error("fenêtre intégrée indisponible: %s", e)
            browser = True
    if browser:
        webbrowser.open(target)
        if server is None:
            return 0
        print(f"omni : {server.url}  (Ctrl+C ou « Arrêter omni » dans les réglages pour quitter)")
        try:
            while server.thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        stop()
        return 0

    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window("omni", target, width=1500, height=950, min_size=(1000, 640),
                                   background_color="#0b0b10", text_select=True)

    def pick(title: str) -> str:
        res = window.create_file_dialog(webview.FileDialog.FOLDER, allow_multiple=False)
        return res[0] if res else ""
    windows.set_picker(pick)

    def closing():
        """Closing the window ends the server, and with it a conversion half way through: ask first."""
        n = _running_jobs(url)
        return not n or _confirm(f"{n} travail(aux) en cours seront interrompus.\n\nFermer omni quand même ?")
    window.events.closing += closing
    storage = CONFIG.workspace / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    try:
        webview.start(private_mode=False, storage_path=str(storage), icon=icon_path())
    except Exception as e:  # noqa: BLE001 - no WebView2 runtime: the browser still works
        windows.set_picker(None)                 # the dialog it registered belongs to a window that never opened
        log.error("fenêtre intégrée impossible: %s", e, exc_info=True)
        webbrowser.open(target)
        if server is not None:
            try:
                while server.thread.is_alive():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
    stop()
    return 0
