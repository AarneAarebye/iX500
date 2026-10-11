"""Trims the scanner's background from the sides of a scanned page.

The driver's own crop (`swcrop`) is off: it cut into a real letter's
text. Background is told from paper by texture and brightness together.
With the driver's processing off, a column of the iX500's ADF background
barely varies top to bottom (standard deviation 0.3-0.5, measured
2026-10-11 in gray and colour) and sits at 233-241. Paper varies more --
recycled or textured paper 6-10 -- and smooth office paper, nearly as even
(2.0-2.7), is far brighter (248). A narrow sheet in the A4-wide scan area
leaves such a background strip at its sides.

Length needs nothing here: the scanner's own paper-end sensor (`ald`)
already ends each page where the paper ends.
"""

from __future__ import annotations

import math

from PIL import Image, ImageStat

# Columns are measured on a 1/4-size copy, which is plenty for an edge and
# keeps a 300 DPI page fast.
_SCALE = 4
# A column is background when its brightness varies less than this top to
# bottom AND it is no brighter than _BACKGROUND_MAX. Measured: background
# 0.3-0.5 at 233-241; paper 6-10, smooth white paper 2.0-2.7 at 248. The
# brightness limit sits halfway between, so a slightly brighter background
# (lamp warm-up, another resolution) still counts.
_EVEN_STDDEV = 1.5
_BACKGROUND_MAX = 245
# Only a strip at least this wide is trimmed, so a narrow even area inside
# a document (a ruled border, a blank gutter) is never taken for background.
_MIN_STRIP_MM = 8.0
# Real paper is never this narrow (a business card is about 54 mm). A trim
# that would leave less found no paper edge at all -- typically a blank side,
# which reads as even almost everywhere -- so the page is kept whole and the
# blank check decides. Seen on a real blank sheet: trimmed to 5 pixels, it no
# longer looked blank and ended up in the PDF.
_MIN_PAPER_MM = 30.0


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


def _is_background(column: ImageStat.Stat) -> bool:
    return column.stddev[0] < _EVEN_STDDEV and column.mean[0] <= _BACKGROUND_MAX


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
        _is_background(ImageStat.Stat(small.crop((x, 0, x + 1, height))))
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
    if (right - left) * _SCALE < _MIN_PAPER_MM / 25.4 * dpi:
        return image

    out = image.crop((left * _SCALE, 0, min(image.width, right * _SCALE), image.height))
    out.info["dpi"] = image.info.get("dpi", (dpi, dpi))
    return out
