"""Cross-process lock on a file (batch workers updating one shared file: registry, Lua list)."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path, timeout: float = 60.0, stale: float = 120.0):
    """Hold ``path`` (created exclusively) for the duration of the block. A lock older than ``stale`` seconds was
    left by a dead process and is taken over; after ``timeout`` seconds the block runs anyway (a lock is never
    worth a failed build)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd = None
    while fd is None:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > stale:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                break
            time.sleep(0.02)
        except PermissionError:          # Windows: the lock file is being deleted by its owner
            if time.monotonic() > deadline:
                break
            time.sleep(0.02)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
