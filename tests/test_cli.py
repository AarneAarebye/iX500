from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader

from scanix500.capture import PagePair
from scanix500.cli import main
from scanix500.errors import MultiFeedError


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


def test_main_creates_missing_destination_directory(tmp_path):
    destination = tmp_path / "nested" / "scans"
    pages = [PagePair(_content_image(), _content_image())]

    with patch("scanix500.cli.PySaneDevice"), patch(
        "scanix500.cli.capture_pages", return_value=pages
    ):
        exit_code = main([str(destination), "--skip-ocr"])

    assert exit_code == 0
    assert destination.is_dir()
    assert len(list(destination.glob("scan_*.pdf"))) == 1


def test_main_reports_all_blank_batch_without_writing_pdf(tmp_path, capsys):
    pages = [PagePair(_blank_image(), _blank_image())]

    with patch("scanix500.cli.PySaneDevice"), patch(
        "scanix500.cli.capture_pages", return_value=pages
    ):
        exit_code = main([str(tmp_path), "--skip-ocr"])

    assert exit_code == 0
    assert list(tmp_path.glob("scan_*.pdf")) == []
    assert "All pages were blank" in capsys.readouterr().out


def test_main_processes_partial_batch_after_multi_feed(tmp_path, capsys):
    partial = [PagePair(_content_image(), _content_image())]

    with patch("scanix500.cli.PySaneDevice"), patch(
        "scanix500.cli.capture_pages",
        side_effect=MultiFeedError(sheet_index=2, pages_captured=partial),
    ):
        exit_code = main([str(tmp_path), "--skip-ocr"])

    assert exit_code == 1
    output_files = list(tmp_path.glob("scan_*.pdf"))
    assert len(output_files) == 1
    assert len(PdfReader(str(output_files[0])).pages) == 2
    assert "Multi-feed detected at sheet 2" in capsys.readouterr().err


def test_cli_passes_scan_settings_to_the_device(tmp_path):
    pages = [PagePair(_content_image(), None)]
    with patch("scanix500.cli.PySaneDevice") as device_cls, patch(
        "scanix500.cli.capture_pages", return_value=pages
    ) as capture, patch("scanix500.cli.build_pdf"):
        main([str(tmp_path), "--simplex", "--mode", "gray", "--resolution", "600", "--skip-ocr"])
    device_cls.assert_called_once_with(duplex=False, color_mode="gray", resolution=600)
    assert capture.call_args.kwargs == {"duplex": False}


def test_cli_bw_thresholds_pages_before_building_the_pdf(tmp_path):
    pages = [PagePair(_content_image(), None)]
    with patch("scanix500.cli.PySaneDevice"), patch(
        "scanix500.cli.capture_pages", return_value=pages
    ), patch("scanix500.cli.build_pdf") as build:
        main([str(tmp_path), "--simplex", "--mode", "bw", "--skip-ocr", "--skip-blank-filter"])
    images = build.call_args.args[0]
    assert {img.mode for img in images} == {"1"}


def test_cli_keep_blank_pages_reaches_the_filter(tmp_path):
    pages = [PagePair(_content_image(), None)]
    with patch("scanix500.cli.PySaneDevice"), patch(
        "scanix500.cli.capture_pages", return_value=pages
    ), patch("scanix500.cli.filter_pages", return_value=[[pages[0].front]]) as filt, patch("scanix500.cli.build_pdf"):
        main([str(tmp_path), "--split-on-blank", "--keep-blank-pages", "--skip-ocr"])
    assert filt.call_args.kwargs == {"split_on_blank": True, "drop_blank_pages": False}
