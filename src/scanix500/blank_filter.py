from PIL import Image

from scanix500.capture import PagePair


def is_blank(image: Image.Image, threshold: float = 0.02) -> bool:
    gray = image.convert("L")
    histogram = gray.histogram()
    total_pixels = gray.width * gray.height
    non_background = sum(
        count for value, count in enumerate(histogram) if value < 250
    )
    return (non_background / total_pixels) < threshold


def filter_pages(
    pages: list[PagePair],
    *,
    threshold: float = 0.02,
    split_on_blank: bool = False,
) -> list[list[Image.Image]]:
    partitions: list[list[Image.Image]] = [[]]
    for pair in pages:
        front_blank = is_blank(pair.front, threshold)
        back_blank = is_blank(pair.back, threshold)

        if front_blank and back_blank:
            if split_on_blank and partitions[-1]:
                partitions.append([])
            continue

        if not front_blank:
            partitions[-1].append(pair.front)
        if not back_blank:
            partitions[-1].append(pair.back)

    return [p for p in partitions if p]
