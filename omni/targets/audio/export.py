"""Export every sound of a source to a named folder tree (game-independent).

1. hash:     every SoundRef's bytes are hashed (SHA-1); identical sounds reached several ways are stored once.
             Bank copies of the first bytes of a streamed music (prefetch stubs: a few KB claiming minutes of
             audio) are skipped: the full media is exported from its own resource.
2. name:     the copy keeps the best name proposed by the source (lowest priority, then shortest path); the
             other names are kept as aliases in the index.
3. convert:  by the Rust core (wwise.rs in-process: no child process, no temporary file), in parallel threads;
             files already exported are skipped, so an interrupted export resumes where it stopped. Tags (title,
             album, artist, language, genre) go into the files when asked.
4. index:    index.csv (file, sha1, codec, channels, rate, seconds, sources, aliases, title, album, genre,
             language) + summary.json.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

from ...core.ir import SoundRef
from ...native import R
from .wwise import FORMATS, VORBIS, wem_info

MAX_REL = 180          # Windows MAX_PATH: keep room for the output root
_EXTS = (".ogg", ".flac", ".wav", ".mp3")
FIELDS = ["file", "sha1", "codec", "channels", "rate", "seconds", "sources", "aliases", "title", "album", "genre",
          "language"]


def _fit(rel: str, sha: str) -> str:
    """Shorten over-long paths (component by component, then the stem with a hash tag)."""
    parts = [p[:80] for p in rel.split("/")]
    rel = "/".join(parts)
    if len(rel) > MAX_REL:
        stem = parts[-1][: max(16, MAX_REL - len("/".join(parts[:-1])) - 10)]
        rel = "/".join(parts[:-1] + [f"{stem}_{sha[:8]}"])
    return rel


def _existing(base: Path) -> Path | None:
    for e in _EXTS:
        if base.with_name(base.name + e).exists():
            return base.with_name(base.name + e)
    return None


def _flac_seconds(path: Path) -> float | None:
    """Exact duration from the FLAC STREAMINFO block (sample rate 20 bits, total samples 36 bits)."""
    try:
        with open(path, "rb") as f:
            head = f.read(42)
        if head[:4] != b"fLaC":
            return None
        rate = (head[18] << 12) | (head[19] << 4) | (head[20] >> 4)
        total = ((head[21] & 0x0F) << 32) | int.from_bytes(head[22:26], "big")
        return round(total / rate, 3) if rate and total else None
    except OSError:
        return None


def is_stub(info) -> bool:
    """A prefetch copy: Vorbis claiming more than ~4 s of audio in less than 1 kbit/s of data."""
    if info is None or info.codec != VORBIS or not info.samples or not info.rate or not info.data_size:
        return False
    seconds = info.samples / info.rate
    return seconds > 4 and info.data_size * 8 / seconds < 1000


def _tags(ref: SoundRef, sha: str) -> list[tuple[str, str]]:
    m = ref.meta or {}
    out = [(k, str(m[k])) for k in ("title", "album", "artist", "genre", "language", "comment") if m.get(k)]
    out.append(("source", ref.source_id))
    out.append(("encoded_by", "omni"))
    return out


# ------------------------------------------------------------------------------------------------ converters
def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _ffmpeg_encode(pcm_wav: bytes, args: list[str], suffix: str, out: Path) -> bytes:
    """Encode a decoded WAV with ffmpeg into a real file (headers are rewritten once encoding ends)."""
    from ...core.config import CONFIG
    tmp = out.with_name(out.name + ".part" + suffix)
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([CONFIG.ffmpeg, "-v", "error", "-y", "-f", "wav", "-i", "pipe:0", "-map_metadata", "-1", *args, str(tmp)],
                       input=pcm_wav, capture_output=True, creationflags=0x08000000 if os.name == "nt" else 0)
    if r.returncode != 0 or not tmp.exists():
        raise RuntimeError("ffmpeg: " + r.stderr.decode("latin-1", "replace").strip()[-200:])
    data = tmp.read_bytes()
    tmp.unlink(missing_ok=True)
    return data


def _meta_args(tags) -> list[str]:
    return [x for k, v in tags if k in ("title", "album", "artist", "genre", "language", "comment")
            for x in ("-metadata", f"{k}={v}")]


def _mp3(pcm_wav: bytes, channels: int, tags, out: Path) -> bytes:
    # MP3 holds at most 2 channels: surround ambiences are downmixed to stereo
    down = ["-ac", "2"] if channels > 2 else []
    return _ffmpeg_encode(pcm_wav, [*down, "-c:a", "libmp3lame", "-q:a", "0", *_meta_args(tags)], ".mp3", out)


def _vorbis(pcm_wav: bytes, tags, out: Path) -> bytes:
    """Sounds the game does not store as Vorbis (Platinum ADPCM) in an .ogg set: encoded, high quality (~q8)."""
    return _ffmpeg_encode(pcm_wav, ["-c:a", "libvorbis", "-q:a", "8", *_meta_args(tags)], ".ogg", out)


def _job_native(ref: SoundRef, base: str, fmt: str, tags, force: bool = False) -> dict:
    t = Path(base)
    done = None if force else _existing(t)
    data = ref.read()
    if done is not None:
        info = R.wem_info(data) or {}
        secs = _flac_seconds(done) if done.suffix == ".flac" else (
            info["samples"] / info["rate"] if info.get("samples") and info.get("rate") else None)
        return {"file": str(done), "skipped": True, "codec": f"0x{info.get('codec', 0):04X}" if info else "",
                "channels": info.get("channels", ""), "rate": info.get("rate", ""),
                "seconds": round(secs, 3) if secs else None, "bytes": done.stat().st_size}
    try:
        if fmt == "mp3":
            ext, wav, info = R.convert_wem(data, "pcm", [])
            ext, out = ".mp3", _mp3(wav, info["channels"], tags, t)
        elif fmt == "ogg":
            ext, out, info = R.convert_wem(data, "auto", tags)           # game Vorbis rewrapped, unchanged
            if ext != ".ogg":                                            # ADPCM etc.: decoded and encoded
                _e, wav, info = R.convert_wem(data, "pcm", [])
                ext, out = ".ogg", _vorbis(wav, tags, t)
        else:
            ext, out, info = R.convert_wem(data, fmt, tags)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    path = t.with_name(t.name + ext)
    if force:
        for e in _EXTS:                                  # another format of the same sound written earlier
            if e != ext:
                t.with_name(t.name + e).unlink(missing_ok=True)
    _write_atomic(path, out)
    secs = info["samples"] / info["rate"] if info.get("samples") and info.get("rate") else None
    return {"file": str(path), "codec": f"0x{info['codec']:04X}", "channels": info["channels"], "rate": info["rate"],
            "seconds": round(secs, 3) if secs else None, "bytes": len(out)}


def _chunk(items: list, fmt: str, force: bool) -> list[dict]:
    """A batch of conversions in one worker call (fewer round trips between processes)."""
    out = []
    for ref, base, tags in items:
        try:
            out.append(_job_native(ref, base, fmt, tags, force))
        except Exception as e:  # noqa: BLE001
            out.append({"error": f"{type(e).__name__}: {e}"})
    return out


# ------------------------------------------------------------------------------------------------ export
def _hash(ref: SoundRef):
    data = ref.read()
    info = wem_info(data)
    if info is not None:
        info.data_size = min(info.data_size, len(data))      # what is really there (stubs declare more)
    return hashlib.sha1(data).hexdigest(), info


def export_sounds(refs: list[SoundRef], out: Path, fmt: str = "auto", workers: int | None = None,
                  match: str = "", limit: int = 0, progress=print, tags: bool = True, skip_stubs: bool = True,
                  languages: str = "all", cancel=None, on_count=None, force: bool = False,
                  clean: bool = False) -> dict:
    """``on_count(done, total)`` follows the conversion; ``cancel`` (threading.Event) stops it between files.
    ``force`` rewrites files already exported (new tags, new names); ``clean`` then removes the audio files of
    the folder the new index does not list (left by an earlier naming)."""
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}")
    t0 = time.perf_counter()
    if match:
        rx = re.compile(re.escape(match), re.I)
        refs = [r for r in refs if rx.search(r.path)]
    if languages != "all":
        keep = {"english": "english(us)", "neutral": "xx"}.get(languages, languages)
        refs = [r for r in refs if not r.path.startswith("voices/") or r.path.split("/")[1] == keep]
    out.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(workers or os.cpu_count() or 8, 61))      # Windows refuses more worker processes

    # 1 - identical content -> one sound (header read with the hash: stubs are found on the way)
    keys = {}
    for r in refs:
        keys.setdefault((r.file, r.offset, r.size), r)
    progress(f"hashing {len(keys)} media...")
    with ThreadPoolExecutor(min(32, workers * 2)) as ex:
        digest = dict(zip(keys, ex.map(_hash, keys.values(), chunksize=64)))
    stubs = {k for k, (_sha, info) in digest.items() if skip_stubs and is_stub(info)}
    # media that are not audio (Wwise plugin data such as convolution impulse responses: "PLUG" blocks)
    other = {k for k, (_sha, info) in digest.items() if info is None}
    stubs |= other
    groups: dict[str, list[SoundRef]] = defaultdict(list)
    for r in refs:
        k = (r.file, r.offset, r.size)
        if k not in stubs:
            groups[digest[k][0]].append(r)
    progress(f"{len(refs)} references -> {len(groups)} unique sounds"
             + (f" ({len(stubs) - len(other)} prefetch stubs, {len(other)} non-audio media skipped)" if stubs else ""))

    # 2 - best name per sound, unique (case-insensitive) output paths
    plan, taken = [], set()
    for sha, rs in sorted(groups.items(), key=lambda kv: min((r.priority, r.path) for r in kv[1])):
        rs.sort(key=lambda r: (r.priority, len(r.path), r.path))
        rel = _fit(rs[0].path, sha)
        cand, n = rel, 2
        while cand.lower() in taken:
            cand, n = f"{rel}_{n}", n + 1
        taken.add(cand.lower())
        plan.append((sha, cand, rs))
    if limit:
        plan = plan[:limit]

    # 3 - convert
    if not R.has("convert_wem"):
        raise RuntimeError("le cœur Rust est indisponible : reconstruis-le (omni native --build --wasm)")
    # processes, not threads: each has its own core instance and nothing is serialised by the GIL
    progress(f"converting {len(plan)} sounds with {workers} processes (Rust core)")
    rows, errors, done = [], [], 0
    pool = ProcessPoolExecutor(workers)
    size = 64
    chunks = [plan[i:i + size] for i in range(0, len(plan), size)]
    with pool as ex:
        futs = {ex.submit(_chunk, [(rs[0], str(out / cand), _tags(rs[0], sha) if tags else []) for sha, cand, rs in c],
                          fmt, force): c for c in chunks}
        for f in as_completed(futs):
            if cancel is not None and cancel.is_set():
                for x in futs:
                    x.cancel()
                progress("export cancelled: files written so far are kept, a new export resumes from there")
                break
            c = futs[f]
            try:
                results = f.result()
            except Exception as e:  # noqa: BLE001 - a worker died: the whole chunk is reported
                results = [{"error": f"{type(e).__name__}: {e}"}] * len(c)
            for (sha, cand, rs), res in zip(c, results):
                done += 1
                if "error" in res:
                    errors.append({"path": cand, "sources": [r.source_id for r in rs], "error": res["error"]})
                    continue
                m = rs[0].meta or {}
                rows.append({"file": Path(res["file"]).relative_to(out).as_posix(), "sha1": sha,
                             "codec": res.get("codec", ""), "channels": res.get("channels", ""),
                             "rate": res.get("rate", ""), "seconds": res.get("seconds", ""),
                             "sources": " ".join(sorted({r.source_id for r in rs})),
                             "aliases": " | ".join(sorted({r.path for r in rs} - {rs[0].path})),
                             "title": m.get("title", ""), "album": m.get("album", ""), "genre": m.get("genre", ""),
                             "language": m.get("language", "")})
            if on_count:
                on_count(done, len(plan))
            if done % 4096 < size or done == len(plan):
                progress(f"{done}/{len(plan)} converted, {len(errors)} errors, {time.perf_counter() - t0:.0f}s")

    # 4 - index (merged with what an earlier export of other files left, so a filtered export keeps the rest)
    old = {}
    idx = out / "index.csv"
    if idx.exists() and (match or limit or languages != "all"):
        with open(idx, newline="", encoding="utf-8") as fh:
            old = {r["file"]: r for r in csv.DictReader(fh)}
    merged = {**old, **{r["file"]: r for r in rows}}
    all_rows = sorted(merged.values(), key=lambda r: r["file"])
    tmp = idx.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)
    os.replace(tmp, idx)
    removed = 0
    if clean and not (match or limit or languages != "all") and not (cancel is not None and cancel.is_set()):
        listed = {r["file"] for r in all_rows}
        for p in out.rglob("*"):
            if p.suffix in _EXTS and p.is_file() and p.relative_to(out).as_posix() not in listed:
                p.unlink(missing_ok=True)
                removed += 1
        for d in sorted((d for d in out.rglob("*") if d.is_dir()), key=lambda d: -len(d.parts)):
            try:
                d.rmdir()                                  # only succeeds on empty folders
            except OSError:
                pass
        progress(f"{removed} files of an earlier naming removed")
    total = 0
    for r in all_rows:
        try:
            total += (out / r["file"]).stat().st_size
        except OSError:
            pass
    summary = {"format": fmt, "references": len(refs), "unique": len(plan), "written": len(rows),
               "errors": len(errors), "stubs_skipped": len(stubs) - len(other), "non_audio": len(other), "removed": removed, "bytes": total,
               "seconds": round(time.perf_counter() - t0, 1), "engine": "rust",
               "by_type": {e: sum(1 for r in all_rows if r["file"].endswith(e)) for e in _EXTS},
               "cancelled": bool(cancel is not None and cancel.is_set())}
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    if errors:
        (out / "errors.json").write_text(json.dumps(errors, indent=1, ensure_ascii=False), encoding="utf-8")
    return summary
