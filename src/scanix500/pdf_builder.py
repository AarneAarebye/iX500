import logging
import subprocess
import tempfile
from pathlib import Path

from PIL import Image
from pypdf import PdfWriter

logger = logging.getLogger(__name__)


def _save_image_only_pdf(image: Image.Image, path: Path) -> None:
    # Without an explicit resolution Pillow writes 72-DPI page geometry, which
    # turns a 300-DPI A4 scan into a hugely oversized PDF page.
    dpi = image.info.get("dpi", (200, 200))[0]
    image.convert("RGB").save(path, format="PDF", resolution=dpi)


def _merge_pdfs(page_paths: list[Path], output_path: Path) -> None:
    writer = PdfWriter()
    for page_path in page_paths:
        writer.append(str(page_path))
    with open(output_path, "wb") as f:
        writer.write(f)


def _ocr_single_page(input_path: Path) -> Path:
    output_path = input_path.with_suffix(".ocr.pdf")
    try:
        subprocess.run(
            ["ocrmypdf", str(input_path), str(output_path)],
            check=True,
            capture_output=True,
        )
        return output_path
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        logger.warning("OCR failed for %s; including as image-only", input_path)
        return input_path


def build_pdf(images: list[Image.Image], output_path: Path, *, ocr: bool = True) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        page_paths = []
        for i, image in enumerate(images):
            page_path = tmp_dir_path / f"page_{i}.pdf"
            _save_image_only_pdf(image, page_path)
            page_paths.append(_ocr_single_page(page_path) if ocr else page_path)
        _merge_pdfs(page_paths, output_path)
