"""Batch orchestration: parallel conversion in worker processes (no Blender involved)."""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from .core.config import CONFIG

_src = None


def _worker_init(source_name: str, threads: int = 0):
    global _src
    if threads:
        # each worker gets its share of the cores for its native thread pools (Rust/rayon, CoACD/OpenMP):
        # N workers x all cores oversubscribed the CPU (10x slower assets in a 10-worker batch)
        for var in ("RAYON_NUM_THREADS", "OMP_NUM_THREADS"):
            os.environ[var] = str(threads)
    from .cli import _source
    _src = _source(source_name)


def warm_up(source) -> None:
    """Build the shared on-disk caches once, in the parent, before workers start: N processes racing to
    create the same cache files is what broke a first batch (BrokenProcessPool)."""
    try:
        from .sources.glacier.entity import EntityReader
        EntityReader(source).names                      # property-name dictionary
        if hasattr(source, "prop_variants"):
            source.prop_variants(0)                     # mesh -> templates index
    except Exception:  # noqa: BLE001 - caches are an optimisation; workers rebuild them if needed
        pass


QUALITY = {   # base colour, normal, derived maps (roughness/metal, emissive): largest size kept
    "max": (8192, 2048, 4096),     # the game's own resolution for everything but normal maps
    "high": (4096, 2048, 2048),
    "balanced": (2048, 1024, 512),
    "light": (1024, 512, 256),
}


def apply_quality(mat_opts, tex_quality: str) -> None:
    mat_opts.max_size, mat_opts.max_size_normal, mat_opts.max_size_secondary = QUALITY.get(tex_quality, QUALITY["max"])
    mat_opts.max_size_spec = mat_opts.max_size_normal


def make_options(physics: bool = True, collision: str = "game", lossless_normals: bool = False,
                 tex_quality: str = "max"):
    from .core import settings
    from .targets.source.build import BuildOptions
    settings.apply()
    o = BuildOptions(physics=physics, collision=collision)
    o.mat.lossless_normals = lossless_normals
    apply_quality(o.mat, tex_quality)
    return o


def _work(key: str, opt_kw: dict):
    from .targets.source.build import build_model
    return asdict(build_model(_src, key, CONFIG, make_options(**opt_kw)))


def run_batch(source_name: str, keys: list[str], workers: int = 4, physics: bool = True, report: Path | None = None,
              on_result=None, collision: str = "game", lossless_normals: bool = False,
              tex_quality: str = "max", cancel=None) -> dict:
    t0 = time.perf_counter()
    report = report or CONFIG.workspace / "reports" / time.strftime("run_%Y%m%d_%H%M%S.jsonl")
    report.parent.mkdir(parents=True, exist_ok=True)
    stats = {"OK": 0, "PARTIAL": 0, "FAILED": 0}
    from .cli import _source
    warm_up(_source(source_name))
    with open(report, "w", encoding="utf-8") as rf, ProcessPoolExecutor(
            workers, initializer=_worker_init,
            initargs=(source_name, max(1, (os.cpu_count() or 4) // max(1, workers)))) as ex:
        kw = dict(physics=physics, collision=collision, lossless_normals=lossless_normals, tex_quality=tex_quality)
        futs = {ex.submit(_work, k, kw): k for k in keys}
        for fut in as_completed(futs):
            if cancel is not None and cancel.is_set():
                for f in futs:
                    f.cancel()
                stats["cancelled"] = True
                break
            try:
                r = fut.result()
            except Exception as e:  # worker crash
                r = {"key": futs[fut], "status": "FAILED", "errors": [f"{type(e).__name__}: {e}"], "seconds": {}}
            stats[r["status"]] = stats.get(r["status"], 0) + 1
            rf.write(json.dumps(r, ensure_ascii=False) + "\n")
            rf.flush()
            if on_result:
                on_result(r)
    stats["seconds"] = round(time.perf_counter() - t0, 1)
    stats["report"] = str(report)
    return stats
