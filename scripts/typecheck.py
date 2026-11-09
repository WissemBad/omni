"""pyright with a ratchet: the errors that exist today are listed in ``pyright-baseline.json`` (count per file and rule);
a change may remove errors but never add one.

    uv run --no-sync python scripts/typecheck.py            # fails when a file has more errors of a rule than the baseline
    uv run --no-sync python scripts/typecheck.py --update   # rewrite the baseline (after fixing errors: it only shrinks)
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "pyright-baseline.json"


def current() -> Counter:
    out = subprocess.run(["pyright", "--outputjson"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    counts: Counter = Counter()
    for d in json.loads(out)["generalDiagnostics"]:
        if d.get("severity") == "error":
            counts[f"{Path(d['file']).resolve().relative_to(ROOT).as_posix()}::{d.get('rule', '?')}"] += 1
    return counts


def main(argv: list[str]) -> int:
    now = current()
    if "--update" in argv:
        BASELINE.write_text(json.dumps(dict(sorted(now.items())), indent=1) + "\n", encoding="utf-8")
        print(f"baseline: {sum(now.values())} errors in {len(now)} (file, rule) pairs")
        return 0
    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    worse = {k: (base.get(k, 0), n) for k, n in now.items() if n > base.get(k, 0)}
    better = sum(base.get(k, 0) - now.get(k, 0) for k in base if now.get(k, 0) < base[k])
    for k, (was, n) in sorted(worse.items()):
        print(f"NEW  {k}: {n} (baseline {was})")
    print(f"pyright: {sum(now.values())} errors (baseline {sum(base.values())}); {better} fixed since the baseline")
    if worse:
        print("fix the new errors (the baseline never grows)")
        return 1
    if better:
        print("run with --update to lock in the progress")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
