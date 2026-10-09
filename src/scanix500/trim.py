"""Trims the scanner's background from the sides of a scanned page.

The driver's own software crop (`swcrop`) can't find the side edges of a
narrow sheet: the iX500's ADF background is about as light as white paper
(233-247 vs ~225 on a real A5 scan). What does tell them apart is texture.
The background is perfectly even, so a column of it barely varies top to
bottom (standard deviation ~2-3), while paper -- even a blank margin or
the blank back of a sheet -- varies more (6-10, measured on both sides of
a real A5 scan). An A5 sheet in the A4-wide scan area leaves such an even
strip on one side: on the right of the front, mirrored to the left on the
back.

Length needs nothing here: the scanner's own paper-end sensor (`ald`)
already ends each page where the paper ends.
"""

from __future__ import annotations

import math

from PIL import Image, ImageStat

# Columns are measured on a 1/4-size copy, which is plenty for an edge and
# keeps a 300 DPI page fast.
_SCALE = 4
# A column whose brightness varies less than this top to bottom is
# background. Real paper measured 6-10, the background 2-3.
_EVEN_STDDEV = 4.0
# Only a strip at least this wide is trimmed, so a narrow even area inside
# a document (a ruled border, a blank gutter) is never taken for background.
_MIN_STRIP_MM = 8.0


def _even_runs(even: list[bool]) -> list[tuple[int, int]]:
    """(start, end) pairs, end exclusive, of each run of True."""
    runs = []
    start = None
    for i, flag in enumerate(even + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i))
            start = None
    return runs


def trim_background(image: Image.Image) -> Image.Image:
    """Returns the page without an even background strip at its left or
    right side, or the page unchanged when it has none. Keeps the image's
    DPI, which pdf_builder uses for the PDF page size."""
    dpi = image.info.get("dpi", (300, 300))[0]
    small = image.convert("L").reduce(_SCALE)
    width, height = small.size
    if width < 2 or height < 2:
        return image

    even = [
        ImageStat.Stat(small.crop((x, 0, x + 1, height))).stddev[0] < _EVEN_STDDEV
        for x in range(width)
    ]
    min_columns = math.ceil(_MIN_STRIP_MM / 25.4 * dpi / _SCALE)
    strips = [(s, e) for s, e in _even_runs(even) if e - s >= min_columns]

    left, right = 0, width
    middle = width / 2
    # The innermost wide strip on each side marks where the paper ends;
    # anything beyond it (the scan area's own uneven border) goes with it.
    right_strips = [s for s, e in strips if s >= middle]
    left_strips = [e for s, e in strips if e <= middle]
    if right_strips:
        right = min(right_strips)
    if left_strips:
        left = max(left_strips)
    if (left, right) == (0, width):
        return image

    out = image.crop((left * _SCALE, 0, min(image.width, right * _SCALE), image.height))
    out.info["dpi"] = image.info.get("dpi", (dpi, dpi))
    return out
