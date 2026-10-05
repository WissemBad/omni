"""Log files and crash capture.

The packaged app has no console: without this, every error, traceback and native crash disappears. ``setup()`` writes
``<workspace>/logs/omni.log`` (rotating), turns uncaught exceptions of the main and worker threads into log records,
dumps native crashes (access violations) into ``fault.log``, and gives a windowed process (``sys.stderr`` is
``None``) streams that go to the log, so a stray ``print`` is not lost.
"""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import sys
import threading
from pathlib import Path

from .config import CONFIG

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_state: dict = {}


class _Stream:
    """File-like object that writes complete lines to a logger."""

    def __init__(self, logger: logging.Logger, level: int):
        self.logger, self.level, self.buf = logger, level, ""

    def write(self, text: str) -> int:
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self.logger.log(self.level, line)
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def log_dir() -> Path:
    d = CONFIG.workspace / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup(name: str = "omni", console: bool = False) -> Path:
    """Start logging to ``logs/<name>.log``; calling it again does nothing. Worker processes pass their own name
    (one file per process: a rotating file shared between processes cannot be renamed on Windows)."""
    if _state.get("path"):
        return _state["path"]
    path = log_dir() / f"{name}.log"
    handler: logging.Handler
    if name == "omni":
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=4, encoding="utf-8")
    else:
        handler = logging.FileHandler(path, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if console and sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "uvicorn.access", "asyncio", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    log = logging.getLogger("omni")

    def excepthook(kind, value, tb):
        if not issubclass(kind, KeyboardInterrupt):
            log.critical("uncaught exception", exc_info=(kind, value, tb))
        if sys.stderr is not None:
            sys.__excepthook__(kind, value, tb)

    def thread_hook(args):
        if args.exc_type is not SystemExit:
            log.critical("uncaught exception in thread %s", getattr(args.thread, "name", "?"),
                         exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    try:
        fault = open(log_dir() / f"fault-{name}.log", "a", encoding="utf-8")    # kept open for the process lifetime
        faulthandler.enable(file=fault, all_threads=True)
        _state["fault"] = fault
    except OSError:
        pass
    if sys.stderr is None:
        sys.stderr = _Stream(logging.getLogger("stderr"), logging.WARNING)
    if sys.stdout is None:
        sys.stdout = _Stream(logging.getLogger("stdout"), logging.INFO)
    _state["path"] = path
    log.info("log started (%s)", name)
    return path


def tail(lines: int = 200) -> str:
    path = _state.get("path") or log_dir() / "omni.log"
    try:
        return "\n".join(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""
