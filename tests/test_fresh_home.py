"""First start on a PC that has no Omni folder yet: the API answers and writes only inside the new folder."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("httpx2")

SCRIPT = textwrap.dedent("""
    import json
    from fastapi.testclient import TestClient
    from omni.core import settings
    from omni.core.config import CONFIG
    from omni.ui.api import create_app

    settings.apply()
    c = TestClient(create_app(jobs_db="auto"), base_url="http://127.0.0.1:8770")
    out = {}
    out["system"] = c.get("/api/system").status_code
    home = c.get("/api/home").json()
    out["home"] = home["home"]
    out["sources"] = c.get("/api/sources").status_code
    out["jobs"] = c.get("/api/jobs").status_code
    out["games"] = c.get("/api/games").status_code
    out["put"] = c.put("/api/settings", json={"paths": {"exports": ""}, "sounds": {"workers": 3}}).status_code
    out["settings_file"] = CONFIG.settings_file.exists()
    out["pending"] = c.post("/api/home/move", json={"path": CONFIG.home.parent.joinpath("Elsewhere").as_posix()}).json()["pending"]
    out["cancelled"] = c.post("/api/home/cancel").json()["pending"]
    out["bad_move"] = c.post("/api/home/move", json={"path": "relative/dir"}).status_code
    out["inside"] = c.post("/api/home/move", json={"path": str(CONFIG.home / "inner")}).status_code
    print("RESULT " + json.dumps(out))
""")


def test_fresh_omni_folder(tmp_path):
    home = tmp_path / "Omni"
    env = {**os.environ, "OMNI_HOME": str(home), "LOCALAPPDATA": str(tmp_path / "local")}
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-c", SCRIPT], env={**env, "PYTHONPATH": str(root)}, cwd=root, capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    import json
    out = json.loads(next(line for line in r.stdout.splitlines() if line.startswith("RESULT "))[7:])
    assert out["system"] == out["sources"] == out["jobs"] == out["games"] == out["put"] == 200, out
    assert out["home"] == str(home) and out["settings_file"]
    assert out["pending"].endswith("Elsewhere") and out["cancelled"] == ""
    assert out["bad_move"] == 400 and out["inside"] == 400
    assert (home / "workspace" / "config" / "settings.json").is_file()
    assert (home / "workspace" / "state" / "jobs.sqlite").is_file()
