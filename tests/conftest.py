"""Test session setup.

* **Isolation.** Every test session runs in a throwaway Omni folder (``OMNI_HOME``): it never reads or writes the
  settings, the library or the exports of the person running it. ``OMNI_TEST_REAL_HOME=1`` keeps the real folder, for the
  few tests that look for a real game (they skip themselves when there is none).
* **The Rust core is required in CI.** With ``OMNI_REQUIRE_NATIVE=1`` a missing core stops the run, and a test that skips
  itself because of the core fails instead (a broken build must not turn the pipeline green).
* **A synthetic game.** ``synthetic_game`` writes a small HITMAN 3 installation (tests/synthetic.py) and registers it in
  the library, so the API, the catalogs and the sources run without any game file.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

if os.environ.get("OMNI_TEST_REAL_HOME") != "1":
    _home = tempfile.mkdtemp(prefix="omni-test-home-")
    os.environ["OMNI_HOME"] = _home
    os.environ.pop("OMNI_ROOT", None)
    atexit.register(shutil.rmtree, _home, ignore_errors=True)

from omni.core import settings

settings.apply()

REQUIRE_NATIVE = os.environ.get("OMNI_REQUIRE_NATIVE") == "1"
NATIVE_REASONS = ("native", "rust core")


def pytest_sessionstart(session):
    if REQUIRE_NATIVE:
        from omni.native import AVAILABLE, NATIVE_ERROR
        if not AVAILABLE:
            pytest.exit(f"OMNI_REQUIRE_NATIVE=1 but the Rust core is missing: {NATIVE_ERROR}", returncode=2)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if REQUIRE_NATIVE and rep.skipped and isinstance(rep.longrepr, tuple):
        reason = str(rep.longrepr[2]).lower()
        if any(word in reason for word in NATIVE_REASONS):
            rep.outcome = "failed"
            rep.longrepr = f"skipped because of the Rust core while OMNI_REQUIRE_NATIVE=1: {rep.longrepr[2]}"


@pytest.fixture(scope="session")
def synthetic_game(tmp_path_factory):
    """The synthetic HITMAN 3 game (id ``hitman3``) installed and registered in the throwaway library."""
    from synthetic import hash_list, write_game

    from omni import games
    from omni.sources.glacier.hitman import names_dir
    info = games.add(write_game(tmp_path_factory.mktemp("games") / "HITMAN 3"))
    names = names_dir(info["id"])
    names.mkdir(parents=True, exist_ok=True)
    (names / "hash_list.txt").write_text(hash_list(), encoding="utf-8")
    return info


@pytest.fixture(scope="session")
def api_client(synthetic_game):
    """The API as the window talks to it (session cookie included), on the synthetic game."""
    pytest.importorskip("httpx2")
    from fastapi.testclient import TestClient

    from omni.ui.api import create_app
    client = TestClient(create_app(jobs_db=None), base_url="http://127.0.0.1:8770")
    client.get("/")
    for _ in range(200):                    # the catalog of a source is built by a thread on the first request
        if client.get(f"/api/{synthetic_game['id']}/props?limit=1").json().get("total"):
            break
        time.sleep(0.05)
    return client


def pytest_addoption(parser):
    parser.addoption("--update-golden", action="store_true", help="rewrite tests/golden/hashes.json from the current output")


@pytest.fixture
def golden(request):
    """``golden(name, data)``: the SHA-256 of what a writer produced from a fixed input must match tests/golden/hashes.json
    (a change of format is deliberate: ``pytest --update-golden`` rewrites the hashes and the diff shows it)."""
    import hashlib
    import json
    store = Path(__file__).resolve().parent / "golden" / "hashes.json"
    update = request.config.getoption("--update-golden")

    def check(name: str, data: bytes) -> None:
        known = json.loads(store.read_text(encoding="utf-8")) if store.exists() else {}
        digest = hashlib.sha256(data).hexdigest()
        if update:
            known[name] = digest
            store.parent.mkdir(exist_ok=True)
            store.write_text(json.dumps(dict(sorted(known.items())), indent=1) + "\n", encoding="utf-8", newline="\n")
        else:
            assert name in known, f"no golden hash for {name}: pytest --update-golden"
            assert known[name] == digest, f"{name}: the output changed (deliberate? pytest --update-golden)"
    return check
