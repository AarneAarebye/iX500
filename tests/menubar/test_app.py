from pathlib import Path
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
        patch("scanix500.menubar.app.default_pairing_path", return_value=Path("/tmp/scanix500-test-paired-browsers.json")),
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
        patch("scanix500.menubar.app.default_pairing_path", return_value=Path("/tmp/scanix500-test-paired-browsers.json")),
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


def _app_with_pairing(tmp_path):
    from scanix500.menubar.app import ScanixMenuBarApp
    from scanix500.menubar.pairing import PairingStore

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)
    app.pairing = PairingStore(tmp_path / "paired.json")
    return app


def test_pair_browser_shows_a_code_that_pairs(tmp_path):
    app = _app_with_pairing(tmp_path)
    with patch("scanix500.menubar.app.rumps.alert") as alert:
        app._pair_browser(None)
    message = alert.call_args.kwargs["message"]
    digits = "".join(ch for ch in message.split("\n\n")[1] if ch.isdigit())
    assert len(digits) == 6
    assert app.pairing.is_valid(app.pairing.pair(digits))


def test_forget_paired_browsers_only_on_confirm(tmp_path):
    app = _app_with_pairing(tmp_path)
    token = app.pairing.pair(app.pairing.open_window())
    with patch("scanix500.menubar.app.rumps.alert", return_value=0):
        app._forget_paired_browsers(None)
    assert app.pairing.is_valid(token)
    with patch("scanix500.menubar.app.rumps.alert", return_value=1):
        app._forget_paired_browsers(None)
    assert not app.pairing.is_valid(token)


IX500_USB = (0x04C5, 0x132B)
CANOSCAN_USB = (0x04A9, 0x1901)
BOTH_USB = {IX500_USB, CANOSCAN_USB}


def _poller_app(profile, started):
    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)
    app._scanning = False
    app._button_reading = False
    app._button_devices = {}
    app._ticks_since_resolve_attempt = 0
    app.profiles = [profile]
    app._start_scan = lambda p, on_done=None: started.append(p) or True
    return app


def _hardware_profile(**changes):
    from scanix500.menubar.profiles import HARDWARE_BUTTON_PROFILE_NAME
    return Profile(name=HARDWARE_BUTTON_PROFILE_NAME, destination="/tmp/scans", **changes)


def test_read_buttons_reports_the_scanner_and_overrides_of_the_pressed_button():
    from scanix500.menubar import app as app_module

    app = _poller_app(_hardware_profile(), [])
    readers = {"sane:ix500": lambda dev: None,
               "sane:canoscan-8800f": lambda dev: {"color_mode": "bw"} if dev == "pixma:1" else None}
    names = {"sane:ix500": "fujitsu:1", "sane:canoscan-8800f": "pixma:1"}
    with patch.object(app_module, "BUTTON_READERS", readers), patch.object(
        app_module, "resolve_device_name", side_effect=lambda sid: names[sid]
    ):
        assert app._read_buttons(True, BOTH_USB) == ("sane:canoscan-8800f", {"color_mode": "bw"})
    assert app._button_devices == names


def test_read_buttons_returns_none_when_nothing_is_pressed_or_while_scanning():
    from scanix500.menubar import app as app_module

    app = _poller_app(_hardware_profile(), [])
    readers = {"sane:ix500": lambda dev: None}
    with patch.object(app_module, "BUTTON_READERS", readers), patch.object(
        app_module, "resolve_device_name", return_value="fujitsu:1"
    ):
        assert app._read_buttons(True, BOTH_USB) is None
        readers["sane:ix500"] = lambda dev: {}
        app._scanning = True
        assert app._read_buttons(True, BOTH_USB) is None
        app._scanning = False
        assert app._read_buttons(True, BOTH_USB) == ("sane:ix500", {})


def test_read_buttons_never_touches_sane_for_an_absent_scanner_and_forgets_its_name():
    from scanix500.menubar import app as app_module

    app = _poller_app(_hardware_profile(), [])
    app._button_devices = {"sane:canoscan-8800f": "pixma:1", "sane:ix500": "fujitsu:1"}
    read = []
    readers = {"sane:ix500": lambda dev: read.append(dev),
               "sane:canoscan-8800f": lambda dev: read.append(dev)}
    with patch.object(app_module, "BUTTON_READERS", readers), patch.object(
        app_module, "resolve_device_name", return_value="x"
    ) as resolve:
        assert app._read_buttons(True, {IX500_USB}) is None
    resolve.assert_not_called()
    assert read == ["fujitsu:1"]
    assert app._button_devices == {"sane:ix500": "fujitsu:1"}


def test_read_buttons_resolves_a_present_scanner_only_on_a_retry_tick():
    from scanix500.menubar import app as app_module

    app = _poller_app(_hardware_profile(), [])
    with patch.object(app_module, "BUTTON_READERS", {"sane:canoscan-8800f": lambda dev: None}), patch.object(
        app_module, "resolve_device_name", return_value=None
    ) as resolve:
        app._read_buttons(False, BOTH_USB)
        resolve.assert_not_called()
        app._read_buttons(True, BOTH_USB)
        resolve.assert_called_once_with("sane:canoscan-8800f")


class _SyncThread:
    def __init__(self, target, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


def _run_poll(app, ticks=1, ids=BOTH_USB):
    """Runs _poll_button with the worker thread and AppHelper.callAfter made synchronous."""
    from scanix500.menubar import app as app_module

    with patch.object(app_module.threading, "Thread", _SyncThread), patch.object(
        app_module.AppHelper, "callAfter", side_effect=lambda fn, *a: fn(*a)
    ), patch.object(app_module, "usb_ids", return_value=ids) as usb:
        for _ in range(ticks):
            app._poll_button(None)
    return usb


def test_poll_button_starts_a_scan_with_the_scanner_and_mode_of_the_pressed_button():
    from scanix500.menubar import app as app_module

    started = []
    app = _poller_app(_hardware_profile(), started)
    readers = {"sane:ix500": lambda dev: None,
               "sane:canoscan-8800f": lambda dev: {"color_mode": "bw"}}
    names = {"sane:ix500": "fujitsu:1", "sane:canoscan-8800f": "pixma:1"}
    with patch.object(app_module, "BUTTON_READERS", readers), patch.object(
        app_module, "resolve_device_name", side_effect=lambda sid: names[sid]
    ):
        _run_poll(app)

    assert [(p.scanner, p.color_mode, p.destination) for p in started] == [
        ("sane:canoscan-8800f", "bw", "/tmp/scans")]
    assert app._button_reading is False


def test_an_ix500_press_keeps_the_profiles_own_mode():
    from scanix500.menubar import app as app_module

    started = []
    app = _poller_app(_hardware_profile(color_mode="gray"), started)
    with patch.object(app_module, "BUTTON_READERS", {"sane:ix500": lambda dev: {}}), patch.object(
        app_module, "resolve_device_name", return_value="fujitsu:1"
    ):
        _run_poll(app)

    assert [(p.scanner, p.color_mode) for p in started] == [("sane:ix500", "gray")]


def test_poll_button_starts_no_reader_while_scanning_or_while_one_is_running():
    from scanix500.menubar import app as app_module

    app = _poller_app(_hardware_profile(), [])
    with patch.object(app_module.threading, "Thread") as thread:
        app._scanning = True
        app._poll_button(None)
        app._scanning = False
        app._button_reading = True
        app._poll_button(None)
        thread.assert_not_called()
        app._button_reading = False
        app._poll_button(None)
        thread.assert_called_once()
    assert app._button_reading is True  # until the (mocked) worker reports back


def test_poll_button_retries_an_unresolved_scanner_only_every_nth_tick():
    from scanix500.menubar import app as app_module

    app = _poller_app(_hardware_profile(), [])
    with patch.object(app_module, "BUTTON_READERS", {"sane:canoscan-8800f": lambda dev: None}), patch.object(
        app_module, "resolve_device_name", return_value=None
    ) as resolve:
        _run_poll(app, ticks=app_module.RESOLVE_RETRY_TICKS + 1)
    assert resolve.call_count == 2  # tick 0 and tick RESOLVE_RETRY_TICKS


def test_poll_button_reports_back_even_when_the_read_raises():
    from scanix500.menubar import app as app_module

    app = _poller_app(_hardware_profile(), [])
    with patch.object(app_module, "usb_ids", side_effect=RuntimeError("boom")), patch.object(
        app_module.threading, "Thread", _SyncThread
    ), patch.object(app_module.AppHelper, "callAfter", side_effect=lambda fn, *a: fn(*a)):
        app._poll_button(None)
    assert app._button_reading is False
