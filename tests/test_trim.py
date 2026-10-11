import random

from PIL import Image

from scanix500.trim import trim_background

DPI = 300


def _page(columns):
    """A test page built left to right from (kind, width) parts:
    "paper" is textured like real paper, "background" is the scanner's
    perfectly even background, "edge" the uneven strip at the scan area's
    border."""
    rng = random.Random(1)
    height = 400
    width = sum(w for _, w in columns)
    im = Image.new("L", (width, height))
    px = im.load()
    # Paper texture comes in patches (fibres, shading, print), not pixel by
    # pixel, so it survives the 1/4-size copy trim_background() measures on.
    patch = {}
    x0 = 0
    for kind, w in columns:
        for x in range(x0, x0 + w):
            for y in range(height):
                if kind == "background":
                    px[x, y] = 233
                elif kind == "smooth":
                    # Smooth, bright office paper: as even as 2-3, but far
                    # brighter than the background (measured 248 vs 233-241).
                    key = (x // 8, y // 8)
                    if key not in patch:
                        patch[key] = max(0, min(255, int(rng.gauss(248, 2.5))))
                    px[x, y] = patch[key]
                else:
                    key = (x // 8, y // 8)
                    if key not in patch:
                        patch[key] = max(0, min(255, int(rng.gauss(225, 8))))
                    px[x, y] = patch[key]
        x0 += w
    im = im.convert("RGB")
    im.info["dpi"] = (DPI, DPI)
    return im


def test_trims_a_background_strip_on_the_right():
    im = _page([("paper", 420), ("background", 140), ("edge", 40)])
    out = trim_background(im)
    assert abs(out.width - 420) <= 4
    assert out.height == im.height


def test_trims_a_background_strip_on_the_left():
    # The back of a sheet: the strip is mirrored to the other side.
    im = _page([("edge", 20), ("background", 140), ("paper", 440)])
    out = trim_background(im)
    assert abs(out.width - 440) <= 4


def test_keeps_a_page_without_a_background_strip():
    im = _page([("paper", 600)])
    assert trim_background(im).size == im.size


def test_keeps_a_strip_narrower_than_the_minimum():
    # 40 px at 300 DPI is about 3 mm: too narrow to be sure it's background.
    im = _page([("paper", 560), ("background", 40)])
    assert trim_background(im).size == im.size


def test_keeps_the_dpi_for_the_pdf_page_size():
    im = _page([("paper", 420), ("background", 180)])
    assert trim_background(im).info["dpi"] == (DPI, DPI)


def test_never_trims_a_page_down_to_a_sliver():
    # A blank side reads as even almost everywhere; a few uneven columns in
    # the middle (a fold, a shadow) used to leave a 5-pixel page that the
    # blank check then kept. Less than 30 mm left means: keep the page.
    page = _page([("background", 1100), ("paper", 20), ("background", 1100)])
    assert trim_background(page).size == page.size


def test_keeps_a_smooth_bright_paper_margin():
    # A real letter's blank right margin measured as even as 2.0-2.7 but at
    # 248; the background is flatter (0.3-0.5) and grayer (233-241).
    im = _page([("paper", 900), ("smooth", 300)])
    assert trim_background(im).size == im.size


def test_keeps_a_smooth_gray_paper_margin():
    # Gray enough to pass the brightness limit, but not flat like the
    # background: the texture rule alone must keep it.
    rng = random.Random(2)
    im = _page([("paper", 900), ("background", 300)])
    px = im.load()
    patch = {}
    for x in range(900, 1200):
        for y in range(im.height):
            key = (x // 8, y // 8)
            if key not in patch:
                patch[key] = max(0, min(255, int(rng.gauss(238, 2.5))))
            px[x, y] = (patch[key],) * 3
    assert trim_background(im).size == im.size
