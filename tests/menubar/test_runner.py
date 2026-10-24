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


def test_build_argv_adds_only_non_default_settings():
    from scanix500.menubar.runner import _build_argv
    default = _build_argv(Profile(name="p", destination="/d"))
    assert "--simplex" not in default and "--mode" not in default and "--resolution" not in default
    argv = _build_argv(Profile(name="p", destination="/d", duplex=False, color_mode="bw",
                               resolution=600, keep_blank_pages=True))
    assert argv[1:] == ["/d", "--simplex", "--mode", "bw", "--resolution", "600", "--keep-blank-pages"]


SANE_NOISE = "[13:19:42.848668] [bjnp] add_scanner: Scanner MF650C Series is not supported, model is unknown! Please report upstream\n"


def test_sane_debug_lines_never_become_the_message():
    result = parse_scan_output(1, "", SANE_NOISE + "Scanner setup failed: The scanner refused a setting: x\n")
    assert result.message == "Scanner setup failed: The scanner refused a setting: x"


def test_multi_feed_message_skips_sane_noise():
    result = parse_scan_output(1, "/tmp/scan.pdf\n", SANE_NOISE + "Multi-feed detected at sheet 2; processing 2 pages captured before the jam.\n")
    assert result.partial and result.message.startswith("Multi-feed detected at sheet 2")


def test_a_scanner_stop_with_pages_is_partial_and_keeps_the_reason():
    result = parse_scan_output(
        1, "/tmp/scan.pdf\n",
        SANE_NOISE + "Scanner stopped at sheet 3 (Error during device I/O); processing 4 pages captured before it.\n",
    )
    assert result.partial and not result.ok
    assert result.message.startswith("Scanner stopped at sheet 3 (Error during device I/O)")


def test_untimestamped_sane_lines_are_filtered_too():
    result = parse_scan_output(1, "", "[bjnp] add_scanner: Scanner MF650C Series is not supported\nScan failed: x\n")
    assert result.message == "Scan failed: x"


def test_a_stop_before_any_sheet_is_a_plain_failure():
    result = parse_scan_output(1, "", SANE_NOISE + "Scan failed: the scanner stopped at sheet 1 (Document feeder jammed).\n")
    assert not result.ok and not result.partial
    assert result.message == "Scan failed: the scanner stopped at sheet 1 (Document feeder jammed)."


def test_build_argv_names_the_scanner_only_when_it_is_not_the_ix500():
    from scanix500.menubar.runner import _build_argv
    assert "--scanner" not in _build_argv(Profile(name="p", destination="/d"))
    argv = _build_argv(Profile(name="p", destination="/d", scanner="sane:canoscan-8800f", resolution=600))
    assert argv[1:] == ["/d", "--resolution", "600", "--scanner", "sane:canoscan-8800f"]


def test_build_argv_names_format_and_area_only_when_they_are_not_the_defaults():
    from scanix500.menubar.runner import _build_argv
    default = _build_argv(Profile(name="p", destination="/d"))
    assert "--format" not in default and "--area" not in default
    argv = _build_argv(Profile(name="p", destination="/d", scanner="sane:canoscan-8800f", resolution=1200,
                               format="jpeg", area="10x15"))
    assert argv[1:] == ["/d", "--resolution", "1200", "--scanner", "sane:canoscan-8800f",
                        "--format", "jpeg", "--area", "10x15"]
    assert _build_argv(Profile(name="p", destination="/d", format="png"))[1:] == ["/d", "--format", "png"]
