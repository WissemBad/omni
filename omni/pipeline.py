"""Batch orchestration: parallel conversion in worker processes (no Blender involved)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict
from pathlib import Path

from .core.config import CONFIG
from .core.windows import NOWINDOW

log = logging.getLogger("omni.pipeline")

_src = None


def _worker_init(source_name: str, threads: int = 0):
    global _src
    if threads:
        # each worker gets its share of the cores for its native thread pools (Rust/rayon, CoACD/OpenMP):
        # N workers x all cores oversubscribed the CPU (10x slower assets in a 10-worker batch)
        for var in ("RAYON_NUM_THREADS", "OMP_NUM_THREADS"):
            os.environ[var] = str(threads)
    from .cli import _source
    from .core import log as logs
    from .core import settings
    logs.setup(f"worker-{os.getpid()}")
    from .core.windows import tie_children_to_this_process
    tie_children_to_this_process()          # a killed worker (cancel, crash) takes its studiomdl with it
    settings.apply()
    _src = _source(source_name)


def warm_up(source) -> None:
    """Build the shared on-disk caches once, in the parent, before workers start: N processes racing to
    create the same cache files is what broke a first batch (BrokenProcessPool)."""
    try:
        if not hasattr(source, "names"):                # sources without Glacier name/entity caches
            if hasattr(source, "warm_up"):
                source.warm_up()
            return
        from .sources.glacier.entity import EntityReader
        source.names.name(0)                            # names database (built once here, not by every worker)
        EntityReader(source).names                      # property-name dictionary
        if hasattr(source, "ensure_variants"):
            source.ensure_variants()                    # mesh -> templates index
    except Exception as e:  # noqa: BLE001 - caches are an optimisation; workers rebuild them if needed
        log.warning("warm-up skipped: %s: %s", type(e).__name__, e)


MAX_WORKERS = 61          # ProcessPoolExecutor refuses more on Windows


def clamp_workers(n: int | None) -> int:
    return max(1, min(int(n or (os.cpu_count() or 4)), MAX_WORKERS))


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
                 tex_quality: str = "max", apply_settings: bool = True):
    from .core import settings
    from .targets.source.build import BuildOptions
    if apply_settings:                 # a batch applies them once per worker: editing them mid-batch changes nothing
        settings.apply()
    o = BuildOptions(physics=physics, collision=collision)
    o.mat.lossless_normals = lossless_normals
    apply_quality(o.mat, tex_quality)
    return o


def _work(key: str, opt_kw: dict):
    from .targets.source.build import build_model
    return asdict(build_model(_src, key, CONFIG, make_options(apply_settings=False, **opt_kw)))


def _work_pm(cid: str, kw: dict):
    """One playermodel family (all its variations) in a worker process."""
    from .targets.source.pm_build import PMOptions
    from .targets.source.pm_outfit import build_outfit_pm
    c = _src.character(cid)
    if c is None:
        return {"key": cid, "status": "FAILED", "model": "", "errors": [f"unknown character {cid}"], "notes": [],
                "seconds": 0}
    o = PMOptions(max_tris=kw["max_tris"])
    apply_quality(o.mat, kw["tex_quality"])
    o.mat.lossless_normals = kw.get("lossless_normals", False)
    if hasattr(_src, "build_character"):                # sources with their own character builder (Unreal)
        r = _src.build_character(cid, o, CONFIG)
    else:
        r = build_outfit_pm(_src, [int(v["key"], 16) for v in c["variants"]], o, CONFIG, cid.replace("outfit_", ""), "")
    return {"key": cid, "status": r.status, "model": r.model, "errors": r.errors, "notes": r.notes,
            "seconds": r.seconds}


def _pool(workers: int, source_name: str) -> ProcessPoolExecutor:
    threads = max(1, (os.cpu_count() or 4) // max(1, workers))
    # numpy's OpenBLAS starts one thread per core in every worker: N workers x all cores oversubscribed the CPU
    # (studiomdl ran up to 5x slower inside a batch). Read when numpy loads, so it must be in the environment the
    # workers are spawned with (the parent's own numpy is already loaded: unaffected).
    for var in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
        os.environ[var] = str(threads)
    return ProcessPoolExecutor(workers, initializer=_worker_init, initargs=(source_name, threads))


def _kill(ex: ProcessPoolExecutor) -> None:
    """Stop a pool now, workers and the studiomdl they started included (a cancelled batch must not keep going)."""
    procs = list((getattr(ex, "_processes", None) or {}).values())     # shutdown() sets it to None
    ex.shutdown(wait=False, cancel_futures=True)
    for p in procs:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, creationflags=NOWINDOW)
            else:
                p.terminate()
        except OSError:
            pass


def run_batch(source_name: str, keys: list[str], workers: int = 4, physics: bool = True, report: Path | None = None,
              on_result=None, collision: str = "game", lossless_normals: bool = False,
              tex_quality: str = "max", cancel=None) -> dict:
    """Convert the props ``keys`` in worker processes (see run_pool)."""
    kw = dict(physics=physics, collision=collision, lossless_normals=lossless_normals, tex_quality=tex_quality)
    return run_pool(source_name, biggest_first(source_name, keys), _work, kw, workers, report=report,
                    on_result=on_result, cancel=cancel)


def biggest_first(source_name: str, keys: list[str]) -> list[str]:
    """Order props by mesh file size, largest first: a big model started last kept one worker busy for minutes
    while the others had nothing left to do (half the CPU idle at the end of a batch)."""
    try:
        from .cli import _source
        src = _source(source_name)
        if not hasattr(src, "archive"):                 # sources without extracted files: catalog sizes
            from .core.catalog import Catalog
            sizes = {r["key"]: r["size"] for r in Catalog(src).search(limit=10 ** 6, named_only=False)}
            return sorted(keys, key=lambda k: -sizes.get(k, 0))
        archive = src.archive
        size = {}
        for k in keys:
            p = archive.find("PRIM", int(k, 16))
            size[k] = p.stat().st_size if p is not None else 0
        return sorted(keys, key=lambda k: -size[k])
    except Exception as e:  # noqa: BLE001 - an optimisation only
        log.warning("batch order unchanged: %s", e)
        return list(keys)


def run_pm_batch(source_name: str, ids: list[str], workers: int, kw: dict, on_result=None, cancel=None) -> dict:
    """Build the playermodel families ``ids`` in worker processes (see run_pool). ``kw``: max_tris, tex_quality,
    lossless_normals."""
    return run_pool(source_name, ids, _work_pm, kw, workers, on_result=on_result, cancel=cancel,
                    report=CONFIG.workspace / "reports" / time.strftime("pm_%Y%m%d_%H%M%S.jsonl"))


def run_pool(source_name: str, keys: list[str], work, kw: dict, workers: int = 4, report: Path | None = None,
             on_result=None, cancel=None) -> dict:
    """Run ``work(key, kw)`` for every key in worker processes. A worker that dies (native crash) does not take the
    batch with it: the items that were in flight are replayed one by one in a fresh pool, and the one that kills it
    is FAILED."""
    t0 = time.perf_counter()
    workers = clamp_workers(min(workers, len(keys)) if keys else workers)
    report = report or CONFIG.workspace / "reports" / time.strftime("run_%Y%m%d_%H%M%S.jsonl")
    report.parent.mkdir(parents=True, exist_ok=True)
    stats = {"OK": 0, "PARTIAL": 0, "FAILED": 0, "SKIPPED": 0}
    from .cli import _source
    warm_up(_source(source_name))
    queue = deque(keys)
    inflight: dict = {}
    ex = _pool(workers, source_name)
    window = workers * 3
    cancelled = False

    def record(r: dict) -> None:
        stats[r["status"]] = stats.get(r["status"], 0) + 1
        rf.write(json.dumps(r, ensure_ascii=False) + "\n")
        rf.flush()
        if on_result:
            on_result(r)

    def failed(key: str, why: str) -> dict:
        return {"key": key, "status": "FAILED", "errors": [why], "seconds": {}}

    with open(report, "w", encoding="utf-8") as rf:
        try:
            while queue or inflight:
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    break
                while queue and len(inflight) < window:
                    key = queue.popleft()
                    inflight[ex.submit(work, key, kw)] = key
                done, _ = wait(inflight, timeout=0.5, return_when=FIRST_COMPLETED)
                broken = []
                for fut in done:
                    key = inflight.pop(fut)
                    try:
                        record(fut.result())
                    except BrokenProcessPool:
                        broken.append(key)
                    except Exception as e:  # noqa: BLE001 - a failure of one asset, not of the batch
                        record(failed(key, f"{type(e).__name__}: {e}"))
                if broken:
                    broken += list(inflight.values())
                    inflight.clear()
                    _kill(ex)
                    ex = _pool(1, source_name)
                    for key in broken:                      # one at a time: the crasher is the one that breaks it
                        if cancel is not None and cancel.is_set():
                            cancelled = True
                            break
                        try:
                            record(ex.submit(work, key, kw).result())
                        except BrokenProcessPool:
                            record(failed(key, "the conversion process crashed on this asset"))
                            _kill(ex)
                            ex = _pool(1, source_name)
                        except Exception as e:  # noqa: BLE001
                            record(failed(key, f"{type(e).__name__}: {e}"))
                    ex.shutdown(wait=True)
                    ex = _pool(workers, source_name)
        finally:
            _kill(ex) if cancelled else ex.shutdown(wait=True)
    if cancelled:
        stats["cancelled"] = True
    stats["seconds"] = round(time.perf_counter() - t0, 1)
    stats["report"] = str(report)
    return stats
