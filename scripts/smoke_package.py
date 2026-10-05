"""Start the packaged app (``dist/Omni/Omni.exe``) in server mode and check that it really works:
version, the interface built into the package, the Rust core loaded, a clean shutdown.

    uv run python scripts/smoke_package.py [--exe dist/Omni/Omni.exe] [--port 8799]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=str(ROOT / "dist" / "Omni" / "Omni.exe"))
    ap.add_argument("--port", type=int, default=8799)
    a = ap.parse_args()
    expected = next(line.split('"')[1] for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
                    if line.startswith("version = "))
    base = f"http://127.0.0.1:{a.port}"
    with tempfile.TemporaryDirectory() as home:
        proc = subprocess.Popen([a.exe, "ui", "--no-open", "--port", str(a.port)], env={**os.environ, "OMNI_HOME": home},
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            info = None
            for _ in range(120):
                if proc.poll() is not None:
                    print(proc.stdout.read().decode(errors="replace"))
                    print(f"FAIL: the app exited with {proc.returncode}")
                    return 1
                try:
                    info = json.loads(get(base + "/api/system")[1])
                    break
                except (urllib.error.URLError, OSError, ValueError):
                    time.sleep(0.5)
            if info is None:
                print("FAIL: no answer within 60 s")
                return 1
            problems = []
            if info["version"] != expected:
                problems.append(f"version {info['version']} != {expected}")
            if not info["rust"]["native"]:
                problems.append(f"native module not loaded: {info['rust'].get('native_error')}")
            if not info["rust"]["wasm"]:
                problems.append("WebAssembly core not loaded")
            if not next((t["ok"] for t in info["tools"] if t["key"] == "web"), False):
                problems.append("interface not found in the package")
            status, page = get(base + "/")
            if status != 200 or b"<html" not in page.lower():
                problems.append("the interface page does not load")
            urllib.request.urlopen(urllib.request.Request(base + "/api/shutdown", data=b"", method="POST"), timeout=5)
            for _ in range(40):
                if proc.poll() is not None:
                    break
                time.sleep(0.25)
            else:
                problems.append("did not stop on /api/shutdown")
            if problems:
                print("FAIL:\n  " + "\n  ".join(problems))
                return 1
            print(f"OK: omni {info['version']}, native {info['rust']['native_version']}, wasm, interface served")
            return 0
        finally:
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
