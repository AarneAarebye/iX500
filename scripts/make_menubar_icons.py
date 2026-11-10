"""Draws the three menu bar template icons (draft C, "the page and beam").

Black on transparent, 36x36 px at 144 dpi (macOS shows them 18 pt tall) and
tinted by macOS for light/dark menu bars. The committed PNGs in
src/scanix500/menubar/icons/ are the product; run this script to regenerate
them:  .venv/bin/python scripts/make_menubar_icons.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

S = 8   # supersample
W = 36  # 18 pt @2x icon canvas
INK = (0, 0, 0, 255)
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "src" / "scanix500" / "menubar" / "icons"


def canvas():
    return Image.new("RGBA", (W * S, W * S), (0, 0, 0, 0))


def u(v):
    return int(v * S)


def page(d, x0, y0, x1, y1, fold=5, fill=False, width=2.2):
    pts = [(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)]
    P = [(u(a), u(b)) for a, b in pts]
    if fill:
        d.polygon(P, fill=INK)
    else:
        d.line(P + [P[0]], fill=INK, width=u(width), joint="curve")
    F = [(u(x1 - fold), u(y0)), (u(x1 - fold), u(y0 + fold)), (u(x1), u(y0 + fold))]
    d.line(F, fill=INK, width=u(width * 0.8), joint="curve")


def c_idle():
    im = canvas()
    d = ImageDraw.Draw(im)
    page(d, 9, 5, 27, 31, width=2.2)
    return im


def c_scan():
    im = canvas()
    d = ImageDraw.Draw(im)
    page(d, 9, 5, 27, 31, width=2.2)
    # scan arrow: chevrons pointing down into the page
    d.line([(u(14), u(14)), (u(18), u(18)), (u(22), u(14))], fill=INK, width=u(2.4), joint="curve")
    d.line([(u(14), u(21)), (u(18), u(25)), (u(22), u(21))], fill=INK, width=u(2.4), joint="curve")
    return im


def c_count():
    im = canvas()
    d = ImageDraw.Draw(im)
    for k in (2, 1):  # back sheets: only the top and right edge peek out
        o = 3.2 * k
        d.line([(u(9 + o), u(5 - o + 3.2)), (u(9 + o), u(5 - o)), (u(25 + o), u(5 - o)),
                (u(27 + o), u(7 - o)), (u(27 + o), u(28 - o))],
               fill=INK, width=u(2.0), joint="curve")
    page(d, 6, 8, 24, 33, width=2.2)
    for y in (18, 23):
        d.line([(u(10), u(y)), (u(20), u(y))], fill=INK, width=u(2))
    return im


def down(im):
    return im.resize((W, W), Image.LANCZOS)


ICONS = {"idle": c_idle, "scanning": c_scan, "collecting": c_count}


def main(out_dir=DEFAULT_OUT) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, draw in ICONS.items():
        im = down(draw())
        # Template icons are alpha only: pure black, the alpha channel is the shape.
        black = Image.new("RGBA", im.size, (0, 0, 0, 0))
        black.putalpha(im.getchannel("A"))
        path = out / f"{name}.png"
        black.save(path, dpi=(144, 144))
        paths.append(path)
        print(path)
    return paths


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT)
