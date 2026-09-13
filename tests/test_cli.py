from pathlib import Path
from unittest.mock import patch

from PIL import Image

from scanix500.capture import PagePair
from scanix500.cli import main


def _content_image():
    im = Image.new("L", (100, 100), color=255)
    im.paste(0, (10, 10, 90, 90))
    return im


def test_main_default_flags_produce_one_pdf(tmp_path, capsys):
    pages = [PagePair(_content_image(), _content_image())]

    with patch("scanix500.cli.PySaneDevice"), patch(
        "scanix500.cli.capture_pages", return_value=pages
    ), patch("scanix500.pdf_builder.subprocess.run") as mock_ocr_run:
        mock_ocr_run.side_effect = lambda cmd, check, capture_output: Path(
            cmd[2]
        ).write_bytes(Path(cmd[1]).read_bytes())

        exit_code = main([str(tmp_path)])

    assert exit_code == 0
    output_files = list(tmp_path.glob("scan_*.pdf"))
    assert len(output_files) == 1
    printed = capsys.readouterr().out
    assert str(output_files[0]) in printed


def _blank_image():
    return Image.new("L", (100, 100), color=255)


def test_main_split_on_blank_produces_multiple_pdfs(tmp_path):
    doc1 = PagePair(_content_image(), _content_image())
    separator = PagePair(_blank_image(), _blank_image())
    doc2 = PagePair(_content_image(), _content_image())
    pages = [doc1, separator, doc2]

    with patch("scanix500.cli.PySaneDevice"), patch(
        "scanix500.cli.capture_pages", return_value=pages
    ), patch("scanix500.pdf_builder.subprocess.run") as mock_ocr_run:
        mock_ocr_run.side_effect = lambda cmd, check, capture_output: Path(
            cmd[2]
        ).write_bytes(Path(cmd[1]).read_bytes())

        exit_code = main([str(tmp_path), "--split-on-blank"])

    assert exit_code == 0
    output_files = sorted(tmp_path.glob("scan_*.pdf"))
    assert len(output_files) == 2
