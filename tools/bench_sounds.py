"""Throughput of the sound export on a sample, into a scratch folder.
Usage: uv run python tools/bench_sounds.py <out dir> [workers] [limit] [match]"""
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from omni.sources.registry import get_source
    from omni.targets.audio.export import export_sounds
    out = Path(sys.argv[1])
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 6000
    match = sys.argv[4] if len(sys.argv) > 4 else "voices/english(us)/ai_dialog"
    refs = get_source("007fl").list_sounds(progress=lambda m: None)
    shutil.rmtree(out, ignore_errors=True)
    t = time.perf_counter()
    sm = export_sounds(refs, out, "auto", workers, match=match, limit=limit, progress=lambda m: None)
    dt = time.perf_counter() - t
    print(f"{workers} workers: {sm['written']} files in {dt:.1f} s = {sm['written'] / dt:.0f} files/s, "
          f"engine {sm['engine']}, errors {sm['errors']}")


if __name__ == "__main__":
    main()
