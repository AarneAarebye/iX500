"""Writing a scanned page as a JPEG or PNG file."""

from pathlib import Path

from PIL import Image

JPEG_QUALITY = 92
EXTENSIONS = {"jpeg": "jpg", "png": "png"}


def save_image(image: Image.Image, path: Path, fmt: str, dpi: int) -> None:
    """Writes `image` to `path` as "jpeg" or "png", with the dpi embedded. A
    1-bit page (black & white) stays 1-bit as PNG; JPEG can't be 1-bit, so it
    becomes 8-bit gray there."""
    if fmt == "jpeg":
        if image.mode not in ("L", "RGB"):
            image = image.convert("L" if image.mode == "1" else "RGB")
        image.save(path, "JPEG", quality=JPEG_QUALITY, dpi=(dpi, dpi))
    elif fmt == "png":
        image.save(path, "PNG", dpi=(dpi, dpi))
    else:
        raise ValueError(f"unknown image format: {fmt!r}")
