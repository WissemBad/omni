"""Start the packaged app (``dist/Omni/Omni.exe``) and check that it really works:
``selfcheck`` (every source of the registry imports, the Rust core loads), then in server mode the version, the
interface built into the package, the session cookie, a synthetic HITMAN 3 game added and browsed through the API
(source, catalog, glTF preview), a clean shutdown.

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
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


def get(url: str, headers: dict | None = None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=5) as r:
        return r.status, r.read(), r.headers


def call(base: str, cookie: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Cookie": cookie, **({"Content-Type": "application/json"} if data else {})}
    req = urllib.request.Request(base + path, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read()


def install_synthetic_game(home: str) -> None:
    """Write the synthetic HITMAN 3 game (tests/synthetic.py) and put it in the library of ``home``, with the names of its
    resources as the preparation job would have downloaded them (no network in a smoke test)."""
    os.environ["OMNI_HOME"] = home
    from synthetic import hash_list, write_game

    from omni import games
    from omni.sources.glacier.hitman import names_dir
    games.add(write_game(Path(home) / "HITMAN 3"))
    target = names_dir("hitman3")
    target.mkdir(parents=True, exist_ok=True)
    (target / "hash_list.txt").write_text(hash_list(), encoding="utf-8")


def browse_synthetic_game(base: str, cookie: str) -> list[str]:
    """The packaged sources work: the game is listed, its catalog fills, a prop becomes a .glb."""
    try:
        sources = {s["id"] for s in json.loads(call(base, cookie, "/api/sources")[1])}
        if "hitman3" not in sources:
            return [f"/api/sources does not list the synthetic game ({sorted(sources)})"]
        items = []
        for _ in range(60):
            items = json.loads(call(base, cookie, "/api/hitman3/props?q=crate")[1]).get("items", [])
            if items:
                break
            time.sleep(0.25)
        if not items:
            return ["the catalog of the synthetic game stays empty"]
        status, glb = call(base, cookie, f"/api/hitman3/props/{items[0]['key']}/glb")
        if status != 200 or glb[:4] != b"glTF":
            return [f"the glTF preview of the synthetic prop is not a .glb (status {status})"]
    except Exception as e:  # noqa: BLE001 - reported as a smoke failure
        return [f"synthetic game: {type(e).__name__}: {e}"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=str(ROOT / "dist" / "Omni" / "Omni.exe"))
    ap.add_argument("--port", type=int, default=8799)
    a = ap.parse_args()
    expected = next(line.split('"')[1] for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
                    if line.startswith("version = "))
    base = f"http://127.0.0.1:{a.port}"
    with tempfile.TemporaryDirectory() as home:
        install_synthetic_game(home)
        env = {**os.environ, "OMNI_HOME": home}
        check = subprocess.run([a.exe, "selfcheck"], env=env, capture_output=True, text=True, timeout=120)
        print(check.stdout.strip() or check.stderr.strip())
        if check.returncode != 0:
            print("FAIL: selfcheck (a source or the Rust core is missing from the package)")
            return 1
        proc = subprocess.Popen([a.exe, "ui", "--no-open", "--port", str(a.port)], env=env,
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
            if not next((t["ok"] for t in info["tools"] if t["key"] == "web"), False):
                problems.append("interface not found in the package")
            status, page, headers = get(base + "/")
            if status != 200 or b"<html" not in page.lower():
                problems.append("the interface page does not load")
            # the page hands out the session cookie; the rest of the API refuses a caller without it
            cookie = next((c.split(";", 1)[0] for c in headers.get_all("Set-Cookie") or [] if c.startswith("omni_session_")), "")
            if not cookie:
                problems.append("the interface page sets no session cookie")
            try:
                get(base + "/api/jobs")
                problems.append("/api/jobs answers without the session cookie")
            except urllib.error.HTTPError as e:
                if e.code != 403:
                    problems.append(f"/api/jobs without the session cookie: {e.code}, 403 expected")
            if get(base + "/api/jobs", {"Cookie": cookie})[0] != 200:
                problems.append("/api/jobs refuses the session cookie")
            problems += browse_synthetic_game(base, cookie)
            urllib.request.urlopen(urllib.request.Request(base + "/api/shutdown", data=b"", method="POST",
                                                          headers={"Cookie": cookie}), timeout=5)
            for _ in range(40):
                if proc.poll() is not None:
                    break
                time.sleep(0.25)
            else:
                problems.append("did not stop on /api/shutdown")
            if problems:
                print("FAIL:\n  " + "\n  ".join(problems))
                return 1
            print(f"OK: omni {info['version']}, native {info['rust']['native_version']}, interface served")
            return 0
        finally:
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
