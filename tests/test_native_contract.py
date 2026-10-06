"""The Python side and the Rust core agree: every ``N.<name>`` the application calls exists in the built module,
and the module exports nothing the application does not know about (a function left behind by a port is dead code)."""
import re
from pathlib import Path

import pytest

from omni.native import AVAILABLE, N

pytestmark = pytest.mark.skipif(not AVAILABLE, reason="Rust core unavailable")

ROOT = Path(__file__).resolve().parents[1]
CALL = re.compile(r"\bN\.([A-Za-z_][A-Za-z0-9_]*)")


def _used() -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for f in (ROOT / "omni").rglob("*.py"):
        for name in CALL.findall(f.read_text(encoding="utf-8")):
            used.setdefault(name, []).append(f.relative_to(ROOT).as_posix())
    return used


def test_every_native_call_exists():
    exported = set(dir(N))
    missing = {n: files for n, files in _used().items() if n not in exported}
    assert not missing, missing


def test_no_unused_native_exports():
    # entry points reached some other way: tests, examples, the command line status
    allowed = {"version", "omni_native"}
    unused = sorted(n for n in dir(N) if not n.startswith("_") and n not in _used() and n not in allowed)
    assert not unused, f"exported by the Rust core but never called from omni/: {unused}"
