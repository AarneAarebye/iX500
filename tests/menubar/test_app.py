from unittest.mock import patch

import pytest

pytest.importorskip("rumps")

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


def test_init_survives_bridge_startup_oserror():
    # The bridge is an optional enhancement -- a taken port (OSError, e.g.
    # another scanix500-menubar already running) must not take the whole
    # menu bar app down with it. __init__ must complete, self._bridge_server
    # must be set to None (not left unset, and not the OSError), and the
    # failure must be surfaced via a notification rather than silently
    # swallowed.
    from scanix500.menubar.app import ScanixMenuBarApp

    with (
        patch("scanix500.menubar.app.load_profiles", return_value=[Profile(name="Default", destination="/tmp/scans")]),
        patch("scanix500.menubar.app.default_profiles_path", return_value="/tmp/scanix500-test-profiles.json"),
        patch("scanix500.menubar.app.load_bridge_destination", return_value="/tmp/scans"),
        patch("scanix500.menubar.app.default_bridge_destination_path", return_value="/tmp/scanix500-test-bridge-destination.txt"),
        patch("scanix500.menubar.app.start_bridge_server", side_effect=OSError("Address already in use")),
        patch("scanix500.menubar.app.rumps.notification") as notification,
    ):
        app = ScanixMenuBarApp()

    assert app._bridge_server is None
    assert notification.called
    assert notification.call_args.kwargs["title"] == "Scan bridge unavailable"


def test_init_survives_bridge_startup_valueerror():
    # Same as above, but for a non-numeric SCANIX500_BRIDGE_PORT (which
    # start_bridge_server's own int(os.environ.get(...)) raises ValueError
    # on) rather than a taken port.
    from scanix500.menubar.app import ScanixMenuBarApp

    with (
        patch("scanix500.menubar.app.load_profiles", return_value=[Profile(name="Default", destination="/tmp/scans")]),
        patch("scanix500.menubar.app.default_profiles_path", return_value="/tmp/scanix500-test-profiles.json"),
        patch("scanix500.menubar.app.load_bridge_destination", return_value="/tmp/scans"),
        patch("scanix500.menubar.app.default_bridge_destination_path", return_value="/tmp/scanix500-test-bridge-destination.txt"),
        patch("scanix500.menubar.app.start_bridge_server", side_effect=ValueError("invalid literal for int()")),
        patch("scanix500.menubar.app.rumps.notification") as notification,
    ):
        app = ScanixMenuBarApp()

    assert app._bridge_server is None
    assert notification.called


def test_set_bridge_destination_saves_chosen_folder():
    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)  # skip rumps.App.__init__/AppKit setup
    app._scanning = False
    app.bridge_destination = "/tmp/old-scans"
    app.bridge_destination_path = "/tmp/scanix500-test-bridge-destination.txt"

    with (
        patch("scanix500.menubar.app.pick_folder", return_value="/tmp/new-scans") as pick,
        patch("scanix500.menubar.app.save_bridge_destination") as save,
    ):
        app._set_bridge_destination(None)

    pick.assert_called_once_with("/tmp/old-scans")
    save.assert_called_once_with("/tmp/scanix500-test-bridge-destination.txt", "/tmp/new-scans")
    assert app.bridge_destination == "/tmp/new-scans"


def test_set_bridge_destination_does_nothing_on_cancel():
    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)
    app._scanning = False
    app.bridge_destination = "/tmp/old-scans"
    app.bridge_destination_path = "/tmp/scanix500-test-bridge-destination.txt"

    with (
        patch("scanix500.menubar.app.pick_folder", return_value=None),
        patch("scanix500.menubar.app.save_bridge_destination") as save,
    ):
        app._set_bridge_destination(None)

    save.assert_not_called()
    assert app.bridge_destination == "/tmp/old-scans"


def test_set_bridge_destination_does_nothing_while_scanning():
    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)
    app._scanning = True
    app.bridge_destination = "/tmp/old-scans"

    with patch("scanix500.menubar.app.pick_folder") as pick:
        app._set_bridge_destination(None)

    pick.assert_not_called()
