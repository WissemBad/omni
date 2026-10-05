"""Compare the native (Rust) sound conversion with an existing export (ww2ogg.exe / vgmstream / ffmpeg chain).

For a random sample of index.csv rows, the source media is converted again with omni_native and both files are
decoded to 16-bit PCM by ffmpeg: the samples must be identical (Vorbis: same packets; ADPCM: same decoder).
Usage: uv run python tools/check_audio_native.py [count]
"""
from __future__ import annotations

import csv
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omni.core.config import CONFIG  # noqa: E402
from omni.native import R as N  # noqa: E402
from omni.sources.glacier.audio import parse_bank, parse_event  # noqa: E402
from omni.sources.registry import get_source  # noqa: E402


def media_bytes(src, sid: str) -> bytes:
    kind, rest = sid.split(":", 1)
    h, _, idx = rest.partition("#")
    p = src.archive.find(kind, int(h, 16))
    d = p.read_bytes()
    if kind in ("WWES", "WWEM"):
        return d
    if kind == "WWEV":
        _n, emb, _r = parse_event(d)
        _m, off, sz = emb[int(idx)]
        return d[off:off + sz]
    _m, off, sz = parse_bank(d)[int(idx)]
    return d[off:off + sz]


def pcm(path: Path) -> bytes:
    r = subprocess.run([CONFIG.ffmpeg, "-v", "error", "-i", str(path), "-f", "s16le", "-"], capture_output=True)
    return r.stdout


def main(n: int = 60) -> int:
    root = CONFIG.workspace / "audio" / "007fl"
    rows = list(csv.DictReader(open(root / "index.csv", encoding="utf-8")))
    random.seed(7)
    ogg = [r for r in rows if r["file"].endswith(".ogg")]
    flac = [r for r in rows if r["file"].endswith(".flac")]
    sample = random.sample(ogg, n // 2) + random.sample(flac, n // 2)
    src = get_source("007fl")
    bad = 0
    t_new = 0.0
    sizes = [0, 0]
    with tempfile.TemporaryDirectory() as t:
        for r in sample:
            sid = r["sources"].split()[0]
            data = media_bytes(src, sid)
            t0 = time.perf_counter()
            ext, out, info = N.convert_wem(data, "auto", [("title", "x")])
            t_new += time.perf_counter() - t0
            old = root / r["file"]
            new = Path(t) / ("n" + ext)
            new.write_bytes(out)
            a, b = pcm(old), pcm(new)
            sizes[0] += old.stat().st_size
            sizes[1] += len(out)
            same = a == b and len(a) > 0
            if not same:
                bad += 1
                print("DIFF", r["file"], ext, len(a), len(b), info)
    print(f"{n - bad}/{n} identical PCM; native {t_new / n * 1000:.1f} ms/file; size old {sizes[0]} new {sizes[1]}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 60))
