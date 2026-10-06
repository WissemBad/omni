"""The Omni folder layout, its settings and the move from the earlier layouts."""
import json
from pathlib import Path

import pytest

from omni.core import config as cfgmod
from omni.core import migrate
from omni.core.config import Config


@pytest.fixture(autouse=True)
def _game_closed(monkeypatch):
    monkeypatch.setattr(migrate, "gmod_running", lambda: False)


def _cfg(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.workspace = tmp_path / "Omni" / "workspace"
    return cfg


def test_every_path_hangs_off_the_omni_folder(tmp_path):
    cfg = _cfg(tmp_path)
    home = tmp_path / "Omni"
    assert cfg.home == home and cfg.exports == home / "exports"
    assert cfg.addon_dir("g") == home / "exports" / "g" / "garrysmod-addon"
    assert cfg.sounds_dir("g") == home / "exports" / "g" / "sounds"
    assert cfg.gltf_dir("g") == home / "exports" / "g" / "gltf"
    assert cfg.gma_path("g") == home / "exports" / "g" / "omni_g.gma"
    assert cfg.game_dir("g") == home / "workspace" / "games" / "g"
    assert cfg.settings_file == home / "workspace" / "config" / "settings.json"
    assert cfg.jobs_db == home / "workspace" / "state" / "jobs.sqlite"
    assert cfg.tools == home / "workspace" / "tools"


def test_exports_can_live_elsewhere(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.refresh({"exports": str(tmp_path / "big-disk"), "assets": str(tmp_path / "Assets" / "Sorted")})
    assert cfg.addon_dir("g") == tmp_path / "big-disk" / "g" / "garrysmod-addon"
    assert cfg.assets_sorted == tmp_path / "Assets" / "Sorted"
    cfg.refresh({})
    assert cfg.exports == tmp_path / "Omni" / "exports"
    assert cfg.assets_sorted == tmp_path / "Omni" / "workspace" / "assets" / "Sorted"


def test_addon_dirs_lists_only_existing_addons(tmp_path):
    cfg = _cfg(tmp_path)
    (cfg.exports / "a" / "garrysmod-addon").mkdir(parents=True)
    (cfg.exports / "b" / "sounds").mkdir(parents=True)
    assert cfg.addon_dirs() == [cfg.exports / "a" / "garrysmod-addon"]


def test_home_choice_is_remembered_and_moves_are_deferred(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert cfgmod.stored_home() is None and cfgmod.pending_home() is None
    cfgmod.store_home(tmp_path / "D" / "Omni")
    assert cfgmod.stored_home() == tmp_path / "D" / "Omni"
    cfgmod.request_home(tmp_path / "E" / "Omni")
    assert cfgmod.pending_home() == tmp_path / "E" / "Omni" and cfgmod.stored_home() == tmp_path / "D" / "Omni"
    cfgmod.cancel_home_request()
    assert cfgmod.pending_home() is None
    cfgmod.store_home(None)
    assert not (tmp_path / "local" / "omni" / "home.json").exists()


def _legacy(root: Path) -> None:
    ws = root / "workspace"
    for rel, text in {
        "settings.json": "{}", "games.json": "[]", "jobs.sqlite": "db", "catalog_007fl.sqlite": "cat",
        "textures_007fl.sqlite": "tex", "cache/memo.sqlite": "memo", "names/names.sqlite": "names",
        "addons/omni_007fl/addon.json": "{}", "addons/omni_007fl/models/a.mdl": "mdl",
        "audio/007fl/ogg/index.csv": "csv", "exports/007fl/gltf/a.glb": "glb", "exports/007fl/textures/t.png": "png",
        "gma/omni_007fl.gma": "gma", "../tools/VTFEdit/vtfedit.exe": "mine", "blend/007fl/a.blend": "blend", "../tools/studiomdl-ce/cestudiomdl.exe": "exe",
        "../third_party/studiomdl-ce/x64/cestudiomdl.exe": "old",
    }.items():
        f = (ws / rel).resolve()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")


def test_legacy_layout_moves_into_the_new_one(tmp_path):
    old, home = tmp_path / "old", tmp_path / "Omni"
    _legacy(old)
    cfg = _cfg(tmp_path)
    cfg.gmod = tmp_path / "gmod"
    rep = migrate.apply(old, home, cfg=cfg)
    assert not rep.errors and not rep.skipped
    ws, ex = home / "workspace", home / "exports" / "007fl"
    assert (ws / "config" / "settings.json").read_text() == "{}" and (ws / "state" / "jobs.sqlite").exists()
    assert (ws / "games" / "007fl" / "catalog.sqlite").read_text() == "cat"
    assert (ws / "games" / "007fl" / "textures.sqlite").read_text() == "tex"
    assert (ws / "cache" / "memo.sqlite").exists() and (ws / "names" / "names.sqlite").exists()
    assert (ws / "tools" / "studiomdl-ce" / "cestudiomdl.exe").read_text() == "exe"      # not overwritten by the old copy
    assert (ex / "garrysmod-addon" / "models" / "a.mdl").read_text() == "mdl"
    assert (ex / "sounds" / "ogg" / "index.csv").exists() and (ex / "gltf" / "a.glb").exists()
    assert (ex / "textures" / "t.png").exists() and (ex / "omni_007fl.gma").exists() and (ex / "blend" / "a.blend").exists()
    assert (old / "tools" / "VTFEdit" / "vtfedit.exe").exists() and not (home / "workspace" / "tools" / "VTFEdit").exists()   # not omni's
    assert not (old / "workspace" / "addons").exists() and not (old / "workspace" / "audio" / "007fl").exists()
    assert json.loads((ws / "state" / "migrated.json").read_text())["from"] == str(old)


def test_migration_never_overwrites_and_can_be_repeated(tmp_path):
    old, home = tmp_path / "old", tmp_path / "Omni"
    _legacy(old)
    (home / "workspace" / "config").mkdir(parents=True)
    (home / "workspace" / "config" / "settings.json").write_text('{"keep": true}', encoding="utf-8")
    cfg = _cfg(tmp_path)
    cfg.gmod = tmp_path / "gmod"
    rep = migrate.apply(old, home, cfg=cfg)
    assert [m.src.name for m in rep.skipped] == ["settings.json"]
    assert json.loads((home / "workspace" / "config" / "settings.json").read_text()) == {"keep": True}
    assert (old / "workspace" / "settings.json").exists()                  # left where it was
    again = migrate.apply(old, home, cfg=cfg)
    assert [m.src.name for m in again.skipped] == ["settings.json"] and not again.errors


def test_dry_run_changes_nothing(tmp_path):
    old, home = tmp_path / "old", tmp_path / "Omni"
    _legacy(old)
    rep = migrate.apply(old, home, dry_run=True)
    assert rep.moved and not home.exists() and (old / "workspace" / "addons" / "omni_007fl").is_dir()


def test_open_game_postpones_only_the_addon(tmp_path, monkeypatch):
    old, home = tmp_path / "old", tmp_path / "Omni"
    _legacy(old)
    monkeypatch.setattr(migrate, "gmod_running", lambda: True)
    cfg = _cfg(tmp_path)
    cfg.gmod = tmp_path / "gmod"
    rep = migrate.apply(old, home, cfg=cfg)
    assert [m.src.name for m in rep.skipped] == ["omni_007fl"] and "Garry" in rep.skipped[0].note
    assert (old / "workspace" / "addons" / "omni_007fl" / "models" / "a.mdl").exists()
    assert (home / "exports" / "007fl" / "gltf" / "a.glb").exists() and (home / "workspace" / "config" / "settings.json").exists()
    monkeypatch.setattr(migrate, "gmod_running", lambda: False)
    again = migrate.apply(old, home, cfg=cfg)
    assert not again.skipped and (home / "exports" / "007fl" / "garrysmod-addon" / "models" / "a.mdl").exists()


def test_relocate_moves_workspace_and_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(migrate, "CONFIG", cfg)
    (cfg.workspace / "config").mkdir(parents=True)
    (cfg.workspace / "config" / "settings.json").write_text("{}", encoding="utf-8")
    (cfg.addon_dir("g") / "models").mkdir(parents=True)
    new = tmp_path / "Elsewhere" / "Omni"
    migrate.relocate(new)
    assert (new / "workspace" / "config" / "settings.json").exists()
    assert (new / "exports" / "g" / "garrysmod-addon" / "models").is_dir()
    assert not (tmp_path / "Omni" / "workspace").exists() and cfgmod.stored_home() == new
    full = tmp_path / "Full"
    (full / "x").mkdir(parents=True)
    with pytest.raises(RuntimeError):
        migrate.relocate(full)


def test_addon_namespace_is_a_clean_free_path():
    from omni.core.config import clean_namespace
    assert clean_namespace("omni") == "omni" and clean_namespace("") == "omni"
    assert clean_namespace(" Wissem/Omni/ ") == "wissem/omni"
    assert clean_namespace(r"import\wissem") == "import/wissem"
    assert clean_namespace("../../x y/é") == "x_y"
    cfg = _cfg(Path("."))
    cfg.namespace = "wissem/omni"
    assert cfg.ns("007fl") == "wissem/omni/007fl"


def test_lod_option_reaches_the_build_options():
    from omni.core import settings
    from omni.pipeline import make_options
    assert settings.DEFAULTS["props"]["lods"] is True
    assert make_options(apply_settings=False).lods is True
    assert make_options(apply_settings=False, lods=False).lods is False


def test_data_reset_keeps_settings_library_tools_and_names(tmp_path):
    from omni.core import reset
    cfg = _cfg(tmp_path)
    ws = cfg.workspace
    keep = [ws / "config" / "settings.json", ws / "config" / "games.json", ws / "tools" / "studiomdl-ce" / "x.exe",
            ws / "names" / "names.sqlite", ws / "games" / "g" / "state.json", ws / "state" / "jobs.sqlite"]
    drop = [ws / "cache" / "memo.sqlite", ws / "preview" / "a.glb", ws / "sandbox" / "modelsrc" / "m.smd", ws / "reports" / "r.jsonl",
            ws / "games" / "g" / "catalog.sqlite", ws / "games" / "g" / "textures.sqlite", ws / "games" / "g" / "characters.json",
            cfg.addon_dir("g") / "models" / "a.mdl", cfg.gltf_dir("g") / "a.glb", cfg.sounds_dir("g") / "x.ogg"]
    for f in keep + drop:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")
    res = reset.reset_data(cfg, exports=False)
    assert res["failed"] == 0 and cfg.addon_dir("g").joinpath("models", "a.mdl").exists() and cfg.gltf_dir("g").joinpath("a.glb").exists()
    assert not (ws / "cache").exists() and not (ws / "games" / "g" / "catalog.sqlite").exists()
    res = reset.reset_data(cfg)
    assert res["failed"] == 0 and cfg.addon_dir("g").is_dir() and not list(cfg.addon_dir("g").iterdir())
    assert not cfg.gltf_dir("g").exists() and not cfg.sounds_dir("g").exists()
    assert all(f.exists() for f in keep)


def test_settings_reset_forgets_window_and_viewer_roots(tmp_path, monkeypatch):
    from omni.core import reset, settings
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(settings, "_file", lambda: cfg.settings_file)
    monkeypatch.setattr(settings, "apply", lambda s=None: None)
    cfg.config_dir.mkdir(parents=True)
    for n in ("settings.json", "window.json", "viewer_roots.json", "games.json"):
        (cfg.config_dir / n).write_text("{}", encoding="utf-8")
    reset.reset_settings(cfg)
    assert [p.name for p in cfg.config_dir.iterdir()] == ["games.json"]


def test_monitor_sample_has_the_fields_the_home_page_draws():
    from omni.core import monitor
    s = monitor.sample()
    assert 0 <= s["cpu"] <= 100 and 0 < s["memory"]["percent"] <= 100 and s["memory"]["used"] <= s["memory"]["total"]
    assert s["gpu"] is None or {"name", "load", "temp", "mem_used", "mem_total", "power"} <= set(s["gpu"])
