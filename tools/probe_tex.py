"""Decode some textures of a material to PNG for visual inspection."""
import sys
import pathlib
import time

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "omni"))
from omni.sources.glacier.meta import parse_meta  # noqa: E402
from omni.sources.glacier.texture import decode_mips, to_rgba  # noqa: E402

S = ROOT / "Assets/Sorted"
out = pathlib.Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)


def find(kind, h):
    for c in ("chunk0", "chunk1"):
        p = S / c / kind / f"{h:016X}.{kind}"
        if p.exists():
            return p
    return None


for hs in sys.argv[2:]:
    h = int(hs, 16)
    tp = find("TEXT", h)
    if tp is None:
        print(hs, "TEXT not found"); continue
    meta = parse_meta(open(str(tp) + ".meta", "rb").read())
    texd = None
    for r, f in meta.refs:
        p = find("TEXD", r)
        if p:
            texd = p.read_bytes(); break
    t = time.perf_counter()
    hd, mips = decode_mips(tp.read_bytes(), texd)
    rgba = to_rgba(hd.name, mips[0])
    print(hs, hd.name, "%dx%d" % (hd.width, hd.height), "mips avail", [(m.width) for m in mips], "ts", hd.first_text_mip,
          "texd", texd is not None, "%.3fs" % (time.perf_counter() - t))
    Image.fromarray(rgba).save(out / f"{hs}.png")
