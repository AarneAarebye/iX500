from pathlib import Path
from unittest.mock import patch

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


def test_build_pdf_with_ocr_invokes_ocrmypdf_per_page(tmp_path):
    images = [_content_image(), _content_image()]
    output_path = tmp_path / "out.pdf"

    def fake_run(cmd, check, capture_output):
        # cmd = ["ocrmypdf", str(input_path), str(output_path)]
        input_path, ocred_path = Path(cmd[1]), Path(cmd[2])
        ocred_path.write_bytes(input_path.read_bytes())
        return None

    with patch("scanix500.pdf_builder.subprocess.run", side_effect=fake_run) as mock_run:
        build_pdf(images, output_path, ocr=True)

    assert mock_run.call_count == 2
    reader = PdfReader(str(output_path))
    assert len(reader.pages) == 2
