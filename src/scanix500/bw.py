"""Black & white output. The iX500 scans black & white as gray: the
background trim and the blank-page check need gray levels (in 1-bit the
scanner's background and a white margin look the same), so pages are
thresholded only after both have run."""

from __future__ import annotations

from PIL import Image

# Paper scans at about 225-245, printed text far below 128; 160 keeps light
# pencil and faint print readable. Chosen for typical paper and print; to be confirmed on hardware.
BW_THRESHOLD = 160


def to_black_and_white(image: Image.Image) -> Image.Image:
    out = image.convert("L").point(lambda v: 255 if v >= BW_THRESHOLD else 0, mode="1")
    out.info["dpi"] = image.info.get("dpi", (300, 300))
    return out
