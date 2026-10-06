"""Access to the Rust core (crate in ``native/``, CPython extension ``omni_native`` built by maturin).

``N`` is the extension, wrapped so that a Rust panic reaches Python as ``NativeError`` (PyO3 raises a
``BaseException`` that the ``except Exception`` handlers around conversions would let through). The core is required:
the packaged application ships it, a development checkout builds it with ``python -m omni native --build``.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

CRATE = Path(__file__).resolve().parents[1] / "native"
NATIVE_ERROR = ""


class NativeError(RuntimeError):
    """The Rust core panicked on a file it could not handle."""


class _Guarded:
    """The extension module, every function turned into one that reports a Rust panic as ``NativeError``."""

    def __init__(self, module):
        object.__setattr__(self, "_module", module)

    def __dir__(self):
        return dir(self._module)

    def __getattr__(self, name):
        attr = getattr(self._module, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            try:
                return attr(*args, **kwargs)
            except BaseException as e:  # noqa: BLE001 - only the PyO3 panic type is converted
                if type(e).__name__ == "PanicException":
                    raise NativeError(f"{name}: {e}") from None
                raise
        call.__name__ = name
        call.__doc__ = getattr(attr, "__doc__", None)
        object.__setattr__(self, name, call)          # resolved once
        return call


class _Missing:
    """Stands for the extension when it is not built: any use explains how to build it."""

    def __getattr__(self, name):
        raise RuntimeError(f"le cœur Rust (omni_native) est absent : python -m omni native --build ({NATIVE_ERROR})")


try:
    import omni_native as _raw
    if not hasattr(_raw, "version"):        # an emptied package folder (uv sync removed the extension): not the core
        raise ImportError("omni_native is installed without its extension module (uv sync removes it: use --inexact)")
    N = _Guarded(_raw)
except ImportError as e:  # pragma: no cover - depends on the local build
    NATIVE_ERROR = str(e)
    N = _Missing()

AVAILABLE = not isinstance(N, _Missing)


def status() -> dict:
    return {
        "native": AVAILABLE,
        "native_version": N.version() if AVAILABLE else "",
        "native_error": NATIVE_ERROR,
        "native_functions": sorted(x for x in dir(N) if not x.startswith("_")) if AVAILABLE else [],
    }


def build(release: bool = True) -> int:
    """Compile and install the extension into the current environment (maturin develop)."""
    env = dict(os.environ)
    env["PATH"] = str(Path.home() / ".cargo" / "bin") + os.pathsep + env.get("PATH", "")
    exe = Path(sys.executable).with_name("maturin.exe" if os.name == "nt" else "maturin")
    base = [str(exe)] if exe.exists() else ["uvx", "maturin"]       # no pip in a uv venv: run maturin through uvx
    env["VIRTUAL_ENV"] = str(Path(sys.executable).parents[1])
    # keep the extension that loads today if the new build cannot be loaded (blocked by the system)
    spec = importlib.util.find_spec("omni_native")
    old = Path(spec.submodule_search_locations[0]) / "omni_native.pyd" if spec and spec.submodule_search_locations else None
    backup = old.with_suffix(".pyd.bak") if old and old.exists() else None
    if backup:
        shutil.copyfile(old, backup)
    rc = subprocess.call(base + ["develop", "--uv"] + (["--release"] if release else []), cwd=CRATE, env=env)
    ok = subprocess.call([sys.executable, "-c", "import omni_native"], env=env) == 0
    if not ok and backup:
        shutil.copyfile(backup, old)
        print("the new extension cannot be loaded on this machine: previous one restored")
    return rc if ok else 1
