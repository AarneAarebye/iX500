"""Snaps a page close to a standard paper size to exactly that size.

The paper-end sensor and the background trim measure each sheet, which
leaves an A4 letter at, say, 207 x 296 mm. A page within a few millimetres
of A4, A5, A6, US Letter or US Legal (either way round) is padded with
white or cropped, centred, to exactly that size; anything else (a receipt,
a business card) keeps its measured size.
"""

from __future__ import annotations

from PIL import Image

# (width, height) in mm, portrait; landscape is checked too.
STANDARD_SIZES_MM = {
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "A6": (105.0, 148.0),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
}
# How far a measured side may be from the standard one. A4 and Letter differ
# by 6 mm in width but 18 mm in height, so they can't be confused.
TOLERANCE_MM = 5.0


def _target_mm(w_mm: float, h_mm: float) -> tuple[float, float] | None:
    for w, h in STANDARD_SIZES_MM.values():
        for tw, th in ((w, h), (h, w)):
            if abs(w_mm - tw) <= TOLERANCE_MM and abs(h_mm - th) <= TOLERANCE_MM:
                return tw, th
    return None


def snap_to_standard_size(image: Image.Image) -> Image.Image:
    dpi = image.info.get("dpi", (300, 300))[0]
    target = _target_mm(image.width / dpi * 25.4, image.height / dpi * 25.4)
    if target is None:
        return image
    size = (round(target[0] / 25.4 * dpi), round(target[1] / 25.4 * dpi))
    if size == image.size:
        return image
    white = 255 if image.mode in ("L", "1") else (255,) * len(image.getbands())
    out = Image.new(image.mode, size, white)
    out.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    out.info["dpi"] = image.info.get("dpi", (dpi, dpi))
    return out
