"""Build a small fake game folder (Runtime/chunk0.rpkg) from already extracted resources, to try the onboarding without
the real game. Usage: make_fake_game.py <out folder> [per type]"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rpkg_builder import Res, build  # noqa: E402

from omni.core.config import CONFIG  # noqa: E402
from omni.sources.glacier.meta import parse_meta  # noqa: E402


def main():
    out = Path(sys.argv[1])
    per = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    rnd = random.Random(1)
    res = []
    for t in ("PRIM", "TEXT", "TEXD", "MATI", "TEMP", "TBLU", "BORG", "ALOC", "GFXV"):
        files = [f for f in (CONFIG.assets_sorted / "chunk0" / t).glob(f"*.{t}")][:2000]
        for f in rnd.sample(files, min(per, len(files))):
            m = parse_meta(Path(str(f) + ".meta").read_bytes())
            res.append(Res(m.resource_id, t, f.read_bytes(), m.refs, scramble=rnd.random() < 0.5, compress=rnd.random() < 0.7))
    (out / "Runtime").mkdir(parents=True, exist_ok=True)
    (out / "Runtime" / "chunk0.rpkg").write_bytes(build(res))
    print(f"{len(res)} resources -> {out / 'Runtime' / 'chunk0.rpkg'}")


if __name__ == "__main__":
    main()
