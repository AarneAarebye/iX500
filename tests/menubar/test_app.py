from unittest.mock import patch

from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult


def test_execute_scan_converts_exception_to_failed_scan_result():
    # _execute_scan must never raise -- an uncaught exception here would
    # (in the real _run_scan_thread caller) strand _scanning=True forever,
    # since nothing downstream would ever reset it.
    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)  # skip rumps.App.__init__/AppKit setup
    with patch("scanix500.menubar.app.run_scan", side_effect=RuntimeError("SANE backend crashed")):
        result = app._execute_scan(Profile(name="Default", destination="/tmp/scans"))

    assert result == ScanResult(ok=False, partial=False, message="SANE backend crashed", output_paths=[])


def test_execute_scan_returns_run_scan_result_unchanged_on_success():
    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)
    expected = ScanResult(ok=True, partial=False, message="/tmp/scans/scan_1.pdf", output_paths=["/tmp/scans/scan_1.pdf"])
    with patch("scanix500.menubar.app.run_scan", return_value=expected):
        result = app._execute_scan(Profile(name="Default", destination="/tmp/scans"))

    assert result == expected
