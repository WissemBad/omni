"""The notification-area icon: omni keeps working after its window is closed, and tells when a job is done."""
from __future__ import annotations

import logging

log = logging.getLogger("omni.tray")


class Tray:
    def __init__(self, on_open, on_quit, icon_file: str | None):
        self.on_open, self.on_quit, self.icon_file = on_open, on_quit, icon_file
        self.icon = None

    def start(self) -> bool:
        """Show the icon; False when the platform or the package cannot (the window then asks before closing)."""
        if self.icon is not None:
            return True
        try:
            import pystray
            from PIL import Image
            image = Image.open(self.icon_file) if self.icon_file else Image.new("RGBA", (64, 64), (108, 92, 231, 255))
            menu = pystray.Menu(
                pystray.MenuItem("Ouvrir omni", lambda *_: self.on_open(), default=True),
                pystray.MenuItem("Quitter", lambda *_: self.on_quit()),
            )
            self.icon = pystray.Icon("omni", image, "omni", menu=menu)
            self.icon.run_detached()
            return True
        except Exception:  # noqa: BLE001 - optional comfort, never a reason not to start
            log.warning("tray icon unavailable", exc_info=True)
            self.icon = None
            return False

    def notify(self, title: str, message: str) -> None:
        if self.icon is None:
            return
        try:
            self.icon.notify(message, title)
        except Exception:  # noqa: BLE001
            log.debug("notification failed", exc_info=True)

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass
            self.icon = None
