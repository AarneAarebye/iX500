from PIL import Image

from scanix500.capture import PagePair


# 240, not 250 -- confirmed against a real scanned blank sheet (iX500,
# 300 DPI, Color mode): normal scanner vignetting/paper-texture noise on
# genuinely blank paper puts 35-40% of its pixels in the 240-249 range,
# so a 250 cutoff misses it entirely (measured non-background ratio 0.35
# vs. the 0.02 threshold). 240 drops the same real blank sheet to ~0.01
# while every real content page in the same test batch stayed above 0.03
# -- a >2.5x margin, confirmed empirically, not derived analytically.
_BACKGROUND_PIXEL_CUTOFF = 240


def is_blank(image: Image.Image, threshold: float = 0.02) -> bool:
    gray = image.convert("L")
    histogram = gray.histogram()
    total_pixels = gray.width * gray.height
    non_background = sum(
        count for value, count in enumerate(histogram) if value < _BACKGROUND_PIXEL_CUTOFF
    )
    return (non_background / total_pixels) < threshold


def filter_pages(
    pages: list[PagePair],
    *,
    threshold: float = 0.02,
    split_on_blank: bool = False,
    drop_blank_pages: bool = True,
) -> list[list[Image.Image]]:
    """Groups the pages into documents. A sheet is blank when every side
    it has is blank (a front-only sheet has no back). With split_on_blank a
    blank sheet ends the current document and is dropped. With
    drop_blank_pages (the default) every blank page is dropped; without it,
    blank pages are kept, except a blank sheet used as a separator."""
    partitions: list[list[Image.Image]] = [[]]
    for pair in pages:
        sides = [pair.front] if pair.back is None else [pair.front, pair.back]
        blank = [is_blank(img, threshold) for img in sides]

        if all(blank) and split_on_blank:
            if partitions[-1]:
                partitions.append([])
            continue

        for img, img_blank in zip(sides, blank):
            if not (img_blank and drop_blank_pages):
                partitions[-1].append(img)

    return [p for p in partitions if p]
