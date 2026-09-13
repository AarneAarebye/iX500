import logging
import subprocess
import tempfile
from pathlib import Path

from PIL import Image
from pypdf import PdfWriter

logger = logging.getLogger(__name__)


def _save_image_only_pdf(image: Image.Image, path: Path) -> None:
    image.convert("RGB").save(path, format="PDF")


def _merge_pdfs(page_paths: list[Path], output_path: Path) -> None:
    writer = PdfWriter()
    for page_path in page_paths:
        writer.append(str(page_path))
    with open(output_path, "wb") as f:
        writer.write(f)


def build_pdf(images: list[Image.Image], output_path: Path, *, ocr: bool = True) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        page_paths = []
        for i, image in enumerate(images):
            page_path = tmp_dir_path / f"page_{i}.pdf"
            _save_image_only_pdf(image, page_path)
            page_paths.append(page_path)
        _merge_pdfs(page_paths, output_path)
