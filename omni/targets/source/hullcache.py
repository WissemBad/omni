"""Disk memo of convex decompositions (see core/memo.py)."""
from __future__ import annotations

from ...core.memo import DiskMemo


def _version() -> str:
    try:
        from importlib.metadata import version
        return "1-coacd" + version("coacd")
    except Exception:  # noqa: BLE001
        return "1"


HULLS = DiskMemo("hulls", _version())
