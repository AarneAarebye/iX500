from pathlib import Path
from unittest.mock import patch
import subprocess as subprocess_module

import pytest
from PIL import Image
from pypdf import PdfReader

from scanix500.pdf_builder import OcrFailed, build_pdf, ocr_pdf


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
        # cmd = ["ocrmypdf", "-l", OCR_LANGUAGE, str(input_path), str(output_path)]
        input_path, ocred_path = Path(cmd[-2]), Path(cmd[-1])
        ocred_path.write_bytes(input_path.read_bytes())
        return None

    with patch("scanix500.pdf_builder.subprocess.run", side_effect=fake_run) as mock_run:
        build_pdf(images, output_path, ocr=True)

    assert mock_run.call_count == 2
    reader = PdfReader(str(output_path))
    assert len(reader.pages) == 2


def test_build_pdf_ocr_uses_configured_language(tmp_path):
    images = [_content_image()]
    output_path = tmp_path / "out.pdf"

    def fake_run(cmd, check, capture_output):
        Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())
        return None

    with patch("scanix500.pdf_builder.subprocess.run", side_effect=fake_run) as mock_run:
        build_pdf(images, output_path, ocr=True)

    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "ocrmypdf"
    assert "-l" in cmd
    assert cmd[cmd.index("-l") + 1] == "deu+eng"


def test_build_pdf_falls_back_to_image_only_when_ocr_fails(tmp_path):
    images = [_content_image()]
    output_path = tmp_path / "out.pdf"

    def failing_run(cmd, check, capture_output):
        raise subprocess_module.CalledProcessError(returncode=1, cmd=cmd)

    with patch("scanix500.pdf_builder.subprocess.run", side_effect=failing_run):
        build_pdf(images, output_path, ocr=True)  # must not raise

    reader = PdfReader(str(output_path))
    assert len(reader.pages) == 1


def test_build_pdf_falls_back_when_ocrmypdf_binary_is_missing(tmp_path):
    images = [_content_image()]
    output_path = tmp_path / "out.pdf"

    def missing_binary_run(cmd, check, capture_output):
        raise FileNotFoundError(2, "No such file or directory", "ocrmypdf")

    with patch("scanix500.pdf_builder.subprocess.run", side_effect=missing_binary_run):
        build_pdf(images, output_path, ocr=True)  # must not raise

    reader = PdfReader(str(output_path))
    assert len(reader.pages) == 1


def test_build_pdf_uses_image_dpi_for_page_geometry(tmp_path):
    image = Image.new("RGB", (100, 100), color=(255, 0, 0))
    image.info["dpi"] = (300, 300)
    output_path = tmp_path / "out.pdf"

    build_pdf([image], output_path, ocr=False)

    reader = PdfReader(str(output_path))
    mediabox = reader.pages[0].mediabox
    # 100px at 300 DPI = 1/3 inch = 24pt, not the 100pt a 72-DPI default gives.
    assert float(mediabox.width) == pytest.approx(24.0, abs=0.5)
    assert float(mediabox.height) == pytest.approx(24.0, abs=0.5)


def test_bw_and_gray_pages_are_not_converted_to_color(tmp_path):
    bw = Image.new("1", (100, 100), 1)
    bw.info["dpi"] = (300, 300)
    gray = Image.new("L", (100, 100), 200)
    gray.info["dpi"] = (300, 300)
    out = tmp_path / "out.pdf"
    build_pdf([bw, gray], out, ocr=False)
    reader = PdfReader(str(out))
    spaces = []
    for page in reader.pages:
        xobjects = page["/Resources"]["/XObject"]
        for name in xobjects:
            spaces.append(xobjects[name].get_object()["/ColorSpace"])
    assert "/DeviceRGB" not in spaces


def test_build_pdf_ocr_straightens_slight_tilts(tmp_path):
    # The driver's swdeskew is off (it rotated a straight sheet by 20°);
    # ocrmypdf --deskew straightens only slight tilts and leaves large ones.
    def fake_run(cmd, check, capture_output):
        Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())
        return None

    with patch("scanix500.pdf_builder.subprocess.run", side_effect=fake_run) as mock_run:
        build_pdf([_content_image()], tmp_path / "out.pdf", ocr=True)

    assert "--deskew" in mock_run.call_args.args[0]


def test_ocr_pdf_runs_ocrmypdf_on_the_whole_file(tmp_path):
    src = tmp_path / "in.pdf"
    build_pdf([_content_image(), _content_image()], src, ocr=False)
    out = tmp_path / "out.pdf"

    def fake_run(cmd, check, capture_output):
        Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())

    with patch("scanix500.pdf_builder.subprocess.run", side_effect=fake_run) as run:
        ocr_pdf(src, out, ["fra", "eng"])
    cmd = run.call_args.args[0]
    assert cmd[0] == "ocrmypdf" and cmd[cmd.index("-l") + 1] == "fra+eng" and "--deskew" in cmd
    assert len(PdfReader(str(out)).pages) == 2


def test_ocr_pdf_default_languages(tmp_path):
    src = tmp_path / "in.pdf"
    build_pdf([_content_image()], src, ocr=False)

    def fake_run(cmd, check, capture_output):
        Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())

    with patch("scanix500.pdf_builder.subprocess.run", side_effect=fake_run) as run:
        ocr_pdf(src, tmp_path / "out.pdf", [])
    cmd = run.call_args.args[0]
    assert cmd[cmd.index("-l") + 1] == "deu+eng"


def test_ocr_pdf_falls_back_page_by_page(tmp_path):
    # The whole-file run fails; each page is tried on its own and a page that
    # still fails is kept without text.
    src = tmp_path / "in.pdf"
    build_pdf([_content_image(), _content_image()], src, ocr=False)
    calls = []

    def fake_run(cmd, check, capture_output):
        calls.append(cmd)
        if len(calls) in (1, 3):  # whole file, then page 2
            raise subprocess_module.CalledProcessError(1, cmd)
        Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())

    with patch("scanix500.pdf_builder.subprocess.run", side_effect=fake_run):
        ocr_pdf(src, tmp_path / "out.pdf", ["deu"])
    assert len(calls) == 3 and len(PdfReader(str(tmp_path / "out.pdf")).pages) == 2


def test_ocr_pdf_raises_when_ocrmypdf_is_missing(tmp_path):
    src = tmp_path / "in.pdf"
    build_pdf([_content_image()], src, ocr=False)
    with patch("scanix500.pdf_builder.subprocess.run", side_effect=FileNotFoundError(2, "no", "ocrmypdf")):
        with pytest.raises(OcrFailed):
            ocr_pdf(src, tmp_path / "out.pdf", [])
