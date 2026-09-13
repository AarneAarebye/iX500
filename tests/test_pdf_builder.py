from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from scanix500.pdf_builder import build_pdf


def _content_image():
    return Image.new("RGB", (100, 100), color=(255, 0, 0))


def test_build_pdf_without_ocr_produces_correct_page_count(tmp_path):
    images = [_content_image(), _content_image()]
    output_path = tmp_path / "out.pdf"

    build_pdf(images, output_path, ocr=False)

    reader = PdfReader(str(output_path))
    assert len(reader.pages) == 2
