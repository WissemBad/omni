"""Regression tests of the stability fixes: unique output paths, interrupted names build, catalog swap, settings
bounds, slot roles, compile timeouts. None needs game data."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from omni.core import settings
from omni.core.catalog import Catalog
from omni.core.naming import Names
from omni.sources.glacier import roles
from omni.sources.glacier.adapter import AssetInfo, GlacierSource
from omni.targets.source.build import BIG_MESH, _model_path, compile_timeout


class _FakeNames:
    db_path = Path("names.sqlite")

    def __init__(self, names: dict[int, str]):
        self._names = names

    def name(self, h: int) -> str:
        return self._names.get(h, "")

    def close(self) -> None:
        pass


class _FakeArchive:
    def __init__(self, hashes):
        self._h = {h: Path(f"{h:016X}.PRIM") for h in hashes}

    def index(self, kind):
        return self._h

    def find(self, kind, h):
        return None


def _source(tmp_path: Path, names: dict[int, str]) -> GlacierSource:
    src = object.__new__(GlacierSource)
    src.config = type("C", (), {"cache": tmp_path})()
    src.archive = _FakeArchive(names)
    src.names = _FakeNames(names)
    src._shared = None
    return src


def test_shared_readable_paths_get_a_hash_suffix(tmp_path):
    a = "[assembly:/_knt/environment/geometry/props/military/case_a.wl2?/case_large_a.prim].pc_prim"
    b = "[assembly:/_knt/environment/geometry/props/military/case_a.wl2?/case_large_a.prim^dyn_dynamic.prim].pc_prim"
    c = "[assembly:/_knt/environment/geometry/props/military/other_a.wl2?/vase.prim].pc_prim"
    src = _source(tmp_path, {1: a, 2: b, 3: c})
    paths = {h: src.rel_path(src.info(h)) for h in (1, 2, 3)}
    assert len(set(paths.values())) == 3                      # nothing is overwritten by another asset
    assert paths[3].endswith("/vase")                          # a unique path stays readable


def test_truncated_names_do_not_collide(tmp_path):
    long = "x" * 60
    src = _source(tmp_path, {
        1: f"[assembly:/props/c.wl2?/{long}_a.prim].pc_prim",
        2: f"[assembly:/props/c.wl2?/{long}_b.prim].pc_prim",
    })
    assert src.rel_path(src.info(1)) != src.rel_path(src.info(2))


def test_model_path_stays_short_and_distinct():
    rel = "/".join(["a" * 30] * 5)
    p1 = _model_path("007fl", rel + "1", "0000000000AAAAAA")
    p2 = _model_path("007fl", rel + "2", "0000000000BBBBBB")
    assert len(p1) <= 110 and p1 != p2


def test_unnamed_assets_keep_their_hash():
    info = AssetInfo("0123456789ABCDEF", "", [], "", "", 0)
    assert info.rel_path == "unnamed/0123456789abcdef"


def test_compile_timeout_scales_with_the_mesh():
    assert compile_timeout(0) == 60
    assert compile_timeout(150_000) == 160
    assert compile_timeout(10_000_000) == 600
    assert BIG_MESH > 0


def test_ignored_slots_are_not_unknown():
    assert "mapwindheight" in roles.IGNORED
    assert roles.resolve("mapGREEN_DIRT_Tex_Basecolor", "basic") == "base"
    assert roles.resolve("mapRED_Tex_SRM", "basic") == "srm"


def test_interrupted_names_build_is_rebuilt(tmp_path):
    hash_list = tmp_path / "hash_list.txt"
    hash_list.write_text("0123456789ABCDEF,PRIM,[assembly:/a/b.wl2?/c.prim].pc_prim\n", encoding="utf-8")
    db_path = tmp_path / "names.sqlite"
    db = sqlite3.connect(db_path)                               # what a killed build left behind: an empty table
    db.execute("CREATE TABLE names(h INTEGER PRIMARY KEY, t TEXT, n TEXT)")
    db.commit()
    db.close()
    names = Names(hash_list, db_path)
    assert names.name(0x0123456789ABCDEF).endswith("c.prim].pc_prim")
    assert not list(tmp_path.glob("*.building"))
    names.close()


def test_names_build_failure_leaves_no_database(tmp_path):
    hash_list = tmp_path / "hash_list.txt"
    hash_list.write_text("0123456789ABCDEF,PRIM,x\n", encoding="utf-8")
    db_path = tmp_path / "names.sqlite"
    names = Names(hash_list, db_path)

    def boom(db):
        raise RuntimeError("killed")
    names._build = boom
    with pytest.raises(RuntimeError):
        names.name(1)
    assert not db_path.exists() and not list(tmp_path.glob("*.building"))


class _Rows:
    id = "t"

    def __init__(self, rows):
        self.rows = rows

    def catalog_rows(self):
        return iter(self.rows)


def _row(key, rel, name="n"):
    return {"key": key, "name": name, "rel": rel, "size": 1, "cat": "c", "skinned": False, "linked": False}


def test_catalog_rebuild_swaps_atomically_and_versions(tmp_path):
    path = tmp_path / "cat.sqlite"
    cat = Catalog(_Rows([_row("A", "a"), _row("B", "b")]), path)
    assert cat.build() == 2
    cat.source = _Rows([_row("C", "c")])
    assert cat.build() == 2                                     # not rebuilt unless forced
    assert cat.build(force=True) == 1
    assert [r["key"] for r in cat.search(limit=10)] == ["C"]
    cat.db.close()
    # an old catalog layout (other user_version) is dropped, never reused
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version=1")
    db.commit()
    db.close()
    assert Catalog(_Rows([]), path).count() == 0


def test_settings_numbers_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "_file", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(settings, "_cache", None, raising=False)
    s = settings.save({"props": {"workers": 500}, "sounds": {"workers": 0}})
    assert s["props"]["workers"] == 61 and s["sounds"]["workers"] == 1


def test_concurrent_processes_build_the_names_database_once_each_without_errors(tmp_path):
    """Batch workers all find the database missing at the same time: none may fail, the file must be complete."""
    import os
    import subprocess
    import sys
    import textwrap

    hash_list = tmp_path / "hash_list.txt"
    hash_list.write_text("".join(f"{i:016X},PRIM,[assembly:/a/b{i}.wl2?/c.prim].pc_prim\n" for i in range(1, 20000)),
                         encoding="utf-8")
    code = textwrap.dedent(f"""
        from pathlib import Path
        from omni.core.naming import Names
        n = Names(Path(r"{hash_list}"), Path(r"{tmp_path / 'names.sqlite'}"))
        assert n.name(5).endswith("c.prim].pc_prim"), "missing name"
    """)
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root), "OMNI_HOME": str(tmp_path)}
    procs = [subprocess.Popen([sys.executable, "-c", code], env=env, cwd=root, stderr=subprocess.PIPE, text=True)
             for _ in range(4)]
    errors = [p.communicate()[1] for p in procs]
    assert all(p.returncode == 0 for p in procs), errors
    assert not list(tmp_path.glob("*.building"))


def test_gmod_spawn_script_and_launch_checks(tmp_path):
    from omni.core.config import Config
    from omni.targets.source import gmod

    cfg = Config()
    cfg.workspace = tmp_path / "ws"
    cfg.gmod = tmp_path / "gmod"
    addon = cfg.addon_dir("t")
    lua = gmod.ensure_spawn_script(addon)
    assert lua.read_text(encoding="utf-8") == gmod.SPAWN_LUA and 'concommand.Add("omni_spawn"' in gmod.SPAWN_LUA
    with pytest.raises(FileNotFoundError):                         # the model is not in the addon
        gmod.launch("t", "models/omni/t/x.mdl", "prop", cfg)
    with pytest.raises(ValueError):
        gmod.launch("t", "models/omni/t/x.mdl", "vehicle", cfg)
