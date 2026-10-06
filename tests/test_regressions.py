"""Bugs seen in the field, kept from coming back."""
import time
from pathlib import Path

import pytest

pytest.importorskip("httpx2")


def test_asset_base_ignores_a_game_folder_named_materials(tmp_path):
    """Unreal content has folders called Materials: they sit under models/, the addon root is the folder above."""
    from omni.ui.output import _asset_base
    addon = tmp_path / "garrysmod-addon"
    (addon / "materials" / "ns" / "g").mkdir(parents=True)
    deep = addon / "models" / "ns" / "g" / "materials" / "props"
    deep.mkdir(parents=True)
    (deep / "m.mdl").write_bytes(b"")
    assert _asset_base(addon, "models/ns/g/materials/props/m.mdl") == addon
    solo = tmp_path / "loose"
    (solo / "materials").mkdir(parents=True)
    (solo / "sub").mkdir()
    assert _asset_base(tmp_path, "loose/sub/x.mdl") == solo               # no models/ folder: closest materials/


def test_sound_export_job_runs_with_the_sound_settings(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from omni.core import settings
    from omni.core.config import CONFIG
    from omni.sources import registry
    from omni.ui.api import create_app

    class Fake:
        id, title, capabilities = "fake", "Fake", ("sounds",)

        def list_sounds(self, progress=print, fresh=False):
            return []

    monkeypatch.setattr(CONFIG, "workspace", tmp_path / "Omni" / "workspace")
    monkeypatch.setattr(settings, "_file", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(registry, "get_source", lambda sid: Fake())
    c = TestClient(create_app(jobs_db=None), base_url="http://127.0.0.1:8770")
    job = c.post("/api/fake/sounds/export", json={}).json()["job"]
    for _ in range(100):
        j = c.get(f"/api/jobs/{job}").json()
        if j["phase"] not in ("running", "queued"):
            break
        time.sleep(0.1)
    assert j["phase"] == "done", j.get("error")
    assert Path(CONFIG.sounds_dir("fake")).is_dir()
