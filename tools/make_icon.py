"""Draws the omni icon (omni/assets/omni.ico, web/public/favicon.ico): a violet rounded tile with a ring."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def tile(size: int) -> Image.Image:
    s = size * 4                                          # supersampled
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(s * 0.22)
    # vertical gradient inside a rounded square
    grad = Image.new("RGBA", (s, s))
    for y in range(s):
        t = y / (s - 1)
        grad.paste((int(139 - 40 * t), int(92 - 30 * t), int(246 - 30 * t), 255), (0, y, s, y + 1))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), r, fill=255)
    img.paste(grad, (0, 0), mask)
    cx = s // 2
    ring = int(s * 0.27)
    w = int(s * 0.115)
    d.ellipse((cx - ring, cx - ring, cx + ring, cx + ring), outline=(255, 255, 255, 255), width=w)
    dot = int(s * 0.07)
    d.ellipse((cx - dot, cx - dot, cx + dot, cx + dot), fill=(255, 255, 255, 255))
    return img.resize((size, size), Image.LANCZOS)


sizes = [16, 24, 32, 48, 64, 128, 256]
big = tile(256)
for out in (ROOT / "omni" / "assets" / "omni.ico", ROOT / "web" / "public" / "favicon.ico"):
    out.parent.mkdir(parents=True, exist_ok=True)
    big.save(out, format="ICO", sizes=[(s, s) for s in sizes])
big.save(ROOT / "web" / "public" / "icon.png")
print("ok")
