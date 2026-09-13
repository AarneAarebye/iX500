from unittest.mock import MagicMock, patch

from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import (
    ScanResult,
    _scanix500_executable,
    notification_title,
    parse_scan_output,
    run_scan,
)


def test_parse_scan_output_success_message_names_the_output_path():
    result = parse_scan_output(
        returncode=0,
        stdout="/tmp/scans/scan_2026-09-13_143012.pdf\n",
        stderr="",
    )

    assert result.ok is True
    assert result.partial is False
    assert result.output_paths == ["/tmp/scans/scan_2026-09-13_143012.pdf"]
    assert "/tmp/scans/scan_2026-09-13_143012.pdf" in result.message
    assert result.message != "Scan complete"


def test_parse_scan_output_success_message_names_every_split_output_path():
    result = parse_scan_output(
        returncode=0,
        stdout="/tmp/scans/scan_1.pdf\n/tmp/scans/scan_2.pdf\n",
        stderr="",
    )

    assert "/tmp/scans/scan_1.pdf" in result.message
    assert "/tmp/scans/scan_2.pdf" in result.message


def test_parse_scan_output_no_pages_scanned():
    result = parse_scan_output(returncode=0, stdout="No pages scanned.\n", stderr="")

    assert result == ScanResult(ok=True, partial=False, message="No pages scanned.", output_paths=[])


def test_parse_scan_output_all_blank():
    result = parse_scan_output(
        returncode=0, stdout="All pages were blank; no PDF written.\n", stderr=""
    )

    assert result == ScanResult(
        ok=True,
        partial=False,
        message="All pages were blank; no PDF written.",
        output_paths=[],
    )


def test_parse_scan_output_multi_feed_with_partial_pdf():
    result = parse_scan_output(
        returncode=1,
        stdout="/tmp/scans/scan_2026-09-13_143012.pdf\n",
        stderr="Multi-feed detected at sheet 3; processing 2 pages captured before the jam.\n",
    )

    assert result.ok is False
    assert result.partial is True
    assert result.output_paths == ["/tmp/scans/scan_2026-09-13_143012.pdf"]
    assert "Multi-feed detected at sheet 3" in result.message
    assert "/tmp/scans/scan_2026-09-13_143012.pdf" in result.message


def test_parse_scan_output_multi_feed_with_all_blank_partial_batch():
    result = parse_scan_output(
        returncode=1,
        stdout="All pages were blank; no PDF written.\n",
        stderr="Multi-feed detected at sheet 1; processing 1 pages captured before the jam.\n",
    )

    assert result.ok is False
    assert result.partial is True
    assert result.output_paths == []
    assert "Multi-feed detected at sheet 1" in result.message
    assert "All pages were blank" in result.message


def test_parse_scan_output_generic_failure():
    result = parse_scan_output(
        returncode=1,
        stdout="",
        stderr="Scanner not found: No iX500 found via SANE fujitsu backend\n",
    )

    assert result == ScanResult(
        ok=False,
        partial=False,
        message="Scanner not found: No iX500 found via SANE fujitsu backend",
        output_paths=[],
    )


def test_run_scan_builds_argv_from_profile_flags_and_delegates_to_parse():
    profile = Profile(
        name="Receipts",
        destination="/tmp/receipts",
        skip_blank_filter=True,
        skip_ocr=True,
        split_on_blank=True,
    )
    fake_completed = MagicMock(returncode=0, stdout="/tmp/receipts/scan_1.pdf\n", stderr="")

    with (
        patch(
            "scanix500.menubar.runner._scanix500_executable",
            return_value="/venv/bin/scanix500",
        ),
        patch("scanix500.menubar.runner.subprocess.run", return_value=fake_completed) as mock_run,
    ):
        result = run_scan(profile)

    mock_run.assert_called_once_with(
        [
            "/venv/bin/scanix500",
            "/tmp/receipts",
            "--skip-blank-filter",
            "--skip-ocr",
            "--split-on-blank",
        ],
        capture_output=True,
        text=True,
    )
    assert result.ok is True
    assert result.output_paths == ["/tmp/receipts/scan_1.pdf"]


def test_scanix500_executable_prefers_the_binary_next_to_the_interpreter(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "scanix500").write_text("#!/bin/sh\n")

    with patch("scanix500.menubar.runner.sys.executable", str(bin_dir / "python")):
        assert _scanix500_executable() == str(bin_dir / "scanix500")


def test_scanix500_executable_falls_back_to_path_lookup(tmp_path):
    with (
        patch("scanix500.menubar.runner.sys.executable", str(tmp_path / "bin" / "python")),
        patch("scanix500.menubar.runner.shutil.which", return_value="/usr/local/bin/scanix500"),
    ):
        assert _scanix500_executable() == "/usr/local/bin/scanix500"


def test_scanix500_executable_falls_back_to_bare_name_when_nothing_is_found(tmp_path):
    with (
        patch("scanix500.menubar.runner.sys.executable", str(tmp_path / "bin" / "python")),
        patch("scanix500.menubar.runner.shutil.which", return_value=None),
    ):
        assert _scanix500_executable() == "scanix500"


def test_notification_title_for_a_successful_scan():
    result = ScanResult(ok=True, partial=False, message="/tmp/scan.pdf", output_paths=["/tmp/scan.pdf"])

    assert notification_title(result) == "Scan complete"


def test_notification_title_for_a_partial_scan():
    result = ScanResult(
        ok=False,
        partial=True,
        message="Multi-feed detected at sheet 3 — partial scan saved to /tmp/scan.pdf",
        output_paths=["/tmp/scan.pdf"],
    )

    assert notification_title(result) == "Scan partially completed"


def test_notification_title_for_a_plain_failure():
    result = ScanResult(ok=False, partial=False, message="Scanner not found", output_paths=[])

    assert notification_title(result) == "Scan failed"
