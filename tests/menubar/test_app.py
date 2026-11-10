import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("rumps")

from scanix500.menubar.collecting import FinishOutcome, PageOutcome
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
        patch("scanix500.menubar.app.default_collection_folder", return_value=Path(tempfile.mkdtemp()) / "collection"),
        patch("scanix500.menubar.app.default_collection_config_path", return_value=Path("/tmp/scanix500-test-no-collection.json")),
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
        patch("scanix500.menubar.app.default_collection_folder", return_value=Path(tempfile.mkdtemp()) / "collection"),
        patch("scanix500.menubar.app.default_collection_config_path", return_value=Path("/tmp/scanix500-test-no-collection.json")),
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
    app._collector = None
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


def test_bridge_status_title_shows_the_listening_address_or_that_it_is_down():
    from types import SimpleNamespace

    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)
    app._bridge_server = SimpleNamespace(server_address=("127.0.0.1", 8765))
    assert app._bridge_status_title() == "Listening on 127.0.0.1:8765"

    app._bridge_server = None
    assert app._bridge_status_title() == "Not listening (scan bridge unavailable)"


def test_a_finish_press_starts_no_scan_for_now():
    from unittest.mock import MagicMock

    from scanix500.menubar.app import ScanixMenuBarApp
    from scanix500.menubar.button_watcher import FINISH

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)
    app._button_reading = True
    app._collector = None
    app.profiles = []
    app._start_scan = MagicMock()
    app._on_buttons_read(("sane:canoscan-8800f", FINISH))
    app._start_scan.assert_not_called()
    assert app._button_reading is False


def _icon_app():
    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)  # skip rumps.App.__init__/AppKit setup
    app._icon = app._icon_nsimage = app._title = None
    app._template = True
    app._scanning = False
    return app


def test_icon_path_returns_none_for_a_missing_file():
    from scanix500.menubar import app as app_module

    assert app_module.icon_path("idle").endswith("idle.png")
    assert app_module.icon_path("no-such-icon") is None


def test_scan_start_and_completion_switch_the_icon_with_an_empty_title():
    from scanix500.menubar import app as app_module

    app = _icon_app()
    with patch.object(app_module.threading, "Thread"):
        assert app._start_scan(Profile(name="x", destination="/tmp")) is True
    assert app.icon.endswith("scanning.png") and app.title == ""
    with patch.object(app_module.rumps, "notification"):
        app._on_scan_complete(ScanResult(ok=True, partial=False, message="ok", output_paths=[]))
    assert app.icon.endswith("idle.png") and app.title == ""


def test_a_missing_icon_file_falls_back_to_the_emoji_titles():
    from scanix500.menubar import app as app_module

    app = _icon_app()
    with patch.object(app_module, "icon_path", return_value=None), \
            patch.object(app_module.threading, "Thread"):
        app._start_scan(Profile(name="x", destination="/tmp"))
        assert app.icon is None and app.title == app_module.SCANNING_TITLE
        with patch.object(app_module.rumps, "notification"):
            app._on_scan_complete(ScanResult(ok=True, partial=False, message="ok", output_paths=[]))
    assert app.icon is None and app.title == app_module.IDLE_TITLE


# -- collecting 8800F button pages -----------------------------------------

class _FakeCollector:
    def __init__(self, pages=0):
        self.page_count = pages
        self.calls = []
        self.page_outcome = None
        self.finish_outcome = None
        self.is_expired = False

    @property
    def is_open(self):
        return self.page_count > 0

    def expired(self):
        return self.is_expired

    def scan_page(self, profile, overrides):
        self.calls.append(("scan_page", profile, overrides))
        if isinstance(self.page_outcome, Exception):
            raise self.page_outcome
        if self.page_outcome.ok:
            self.page_count = self.page_outcome.pages
        return self.page_outcome

    def finish(self):
        self.calls.append(("finish",))
        if isinstance(self.finish_outcome, Exception):
            raise self.finish_outcome
        if self.finish_outcome.path is not None:
            self.page_count = 0
        return self.finish_outcome

    def discard(self):
        self.calls.append(("discard",))
        self.page_count = 0


def _collecting_app(pages=0, **profile_changes):
    from scanix500.menubar.app import ScanixMenuBarApp

    app = _icon_app()
    app._button_reading = True
    app._collector = _FakeCollector(pages)
    app.profiles = [_hardware_profile(format="pdf", **profile_changes)]
    app._rebuilds = 0
    app._rebuild_menu = lambda: setattr(app, "_rebuilds", app._rebuilds + 1)
    return app


@pytest.fixture
def sync(monkeypatch):
    """Worker threads and AppHelper.callAfter run immediately; notifications are recorded."""
    from scanix500.menubar import app as app_module

    notes = []
    monkeypatch.setattr(app_module.threading, "Thread", _SyncThread)
    monkeypatch.setattr(app_module.AppHelper, "callAfter", lambda fn, *a: fn(*a))
    monkeypatch.setattr(app_module.rumps, "notification",
                        lambda title, subtitle, message: notes.append((title, message)))
    return notes


CANO = "sane:canoscan-8800f"


def test_collecting_profile_only_for_the_8800f_with_a_pdf_profile():
    app = _collecting_app()
    assert app._collecting_profile(CANO, app.profiles[0]) is True
    assert app._collecting_profile(CANO, _hardware_profile(format="jpeg")) is False
    assert app._collecting_profile(CANO, _hardware_profile(format="png")) is False
    assert app._collecting_profile("sane:ix500", app.profiles[0]) is False
    assert app._collecting_profile(CANO, None) is False
    app._collector = None
    assert app._collecting_profile(CANO, app.profiles[0]) is False


def test_button_routes_to_page_scan_finish_or_the_old_scan_path():
    from scanix500.menubar.button_watcher import FINISH

    app = _collecting_app()
    app._start_scan, app._start_page_scan, app._start_finish = Mock(), Mock(), Mock()

    app._on_buttons_read((CANO, {"color_mode": "bw"}))
    assert app._start_page_scan.call_count == 1
    profile, overrides = app._start_page_scan.call_args.args
    assert profile.scanner == CANO and overrides == {"color_mode": "bw"}
    app._start_scan.assert_not_called()

    app._on_buttons_read((CANO, FINISH))
    app._start_finish.assert_called_once_with()

    app._on_buttons_read(("sane:ix500", {}))
    assert app._start_scan.call_count == 1
    app._on_buttons_read(("sane:ix500", FINISH))
    assert app._start_scan.call_count == 1 and app._start_finish.call_count == 1

    app.profiles = [_hardware_profile(format="jpeg")]
    app._on_buttons_read((CANO, {"color_mode": "bw"}))
    assert app._start_scan.call_count == 2 and app._start_page_scan.call_count == 1
    app._on_buttons_read((CANO, FINISH))  # jpeg: FINISH means nothing
    assert app._start_finish.call_count == 1 and app._start_scan.call_count == 2


def test_a_press_while_scanning_starts_neither_page_nor_finish(sync):
    app = _collecting_app(pages=2)
    app._scanning = True
    assert app._start_page_scan(app.profiles[0], {}) is False
    assert app._start_finish() is False
    assert app._collector.calls == [] and app._scanning is True


def test_first_page_notifies_and_shows_collecting_with_the_count(sync):
    app = _collecting_app()
    app._collector.page_outcome = PageOutcome(True, 1, True, "")
    assert app._start_page_scan(app.profiles[0], {"color_mode": "bw"}) is True
    assert app._scanning is False
    assert app.icon.endswith("collecting.png") and app.title == "1"
    assert sync == [("scanix500", "Collecting pages. Press FINISH when you are done.")]
    assert app._rebuilds == 1
    assert app._collector.calls[0][2] == {"color_mode": "bw"}

    app._collector.page_outcome = PageOutcome(True, 2, False, "")
    app._start_page_scan(app.profiles[0], {})
    assert app.title == "2" and len(sync) == 1  # later pages: no notification
    assert app._rebuilds == 2


def test_a_failed_page_scan_notifies_and_returns_to_collecting_or_idle(sync):
    app = _collecting_app(pages=2)
    app._collector.page_outcome = PageOutcome(False, 2, False, "Scanner jammed")
    app._start_page_scan(app.profiles[0], {})
    assert sync == [("Scan failed", "Scanner jammed")]
    assert app.icon.endswith("collecting.png") and app.title == "2" and app._scanning is False

    app = _collecting_app(pages=0)
    app._collector.page_outcome = PageOutcome(False, 0, False, "Scanner jammed")
    app._start_page_scan(app.profiles[0], {})
    assert app.icon.endswith("idle.png") and app.title == "" and app._scanning is False


def test_an_exception_in_the_page_worker_never_leaves_scanning_set(sync):
    app = _collecting_app()
    app._collector.page_outcome = RuntimeError("boom")
    app._start_page_scan(app.profiles[0], {})
    assert app._scanning is False
    assert sync == [("Scan failed", "boom")]
    assert app.icon.endswith("idle.png")


def test_finish_shows_scanning_then_idle_and_notifies_the_page_count_and_name(sync):
    app = _collecting_app(pages=3)
    seen = []
    real_finish = app._collector.finish
    app._collector.finish = lambda: seen.append((app.icon, app._scanning)) or real_finish()
    app._collector.finish_outcome = FinishOutcome(Path("/tmp/scans/scan_1.pdf"), 3, True, "")
    assert app._start_finish() is True
    assert seen[0][0].endswith("scanning.png") and seen[0][1] is True
    assert app.icon.endswith("idle.png") and app.title == "" and app._scanning is False
    assert sync == [("Scan complete", "3 pages scanned: scan_1.pdf")]
    assert app._rebuilds == 1


def test_finish_of_a_single_page_uses_the_singular_text(sync):
    app = _collecting_app(pages=1)
    app._collector.finish_outcome = FinishOutcome(Path("/tmp/scan_1.pdf"), 1, True, "")
    app._start_finish()
    assert sync == [("Scan complete", "1 page scanned: scan_1.pdf")]


def test_a_failed_finish_notifies_and_returns_to_collecting(sync):
    app = _collecting_app(pages=3)
    app._collector.finish_outcome = FinishOutcome(None, 3, False, "Destination missing")
    app._start_finish()
    assert sync == [("Scan failed", "Destination missing")]
    assert app.icon.endswith("collecting.png") and app.title == "3" and app._scanning is False

    app = _collecting_app(pages=3)
    app._collector.finish_outcome = RuntimeError("boom")
    app._start_finish()
    assert app._scanning is False and app.icon.endswith("collecting.png")


def test_finish_without_text_recognition_says_so(sync):
    app = _collecting_app(pages=2)
    app._collector.finish_outcome = FinishOutcome(Path("/tmp/scan_1.pdf"), 2, False, "tesseract missing")
    app._start_finish()
    title, message = sync[0]
    assert title == "Scan complete"
    assert "2 pages scanned: scan_1.pdf" in message
    assert "the text could not be recognised" in message


def test_finish_with_skip_ocr_does_not_claim_ocr_failed(sync):
    app = _collecting_app(pages=2)
    app._collector.finish_outcome = FinishOutcome(Path("/tmp/scan_1.pdf"), 2, False, "")
    app._start_finish()
    assert "recognised" not in sync[0][1]


def test_collect_tick_finishes_only_an_expired_collection_when_idle(sync):
    app = _collecting_app(pages=2)
    app._collector.finish_outcome = FinishOutcome(Path("/tmp/scan_1.pdf"), 2, True, "")
    app._collect_tick(None)
    assert app._collector.calls == []
    app._collector.is_expired = True
    app._scanning = True
    app._collect_tick(None)
    assert app._collector.calls == []
    app._scanning = False
    app._collect_tick(None)
    assert app._collector.calls == [("finish",)]


def test_start_up_with_a_recovered_collection_shows_collecting(tmp_path):
    from scanix500.menubar import app as app_module

    fake = _FakeCollector(pages=2)
    with (
        patch.object(app_module, "load_profiles", return_value=[Profile(name="Default", destination="/tmp/scans")]),
        patch.object(app_module, "default_profiles_path", return_value="/tmp/scanix500-test-profiles.json"),
        patch.object(app_module, "load_bridge_destination", return_value="/tmp/scans"),
        patch.object(app_module, "default_bridge_destination_path", return_value="/tmp/scanix500-test-bridge-destination.txt"),
        patch.object(app_module, "default_pairing_path", return_value=tmp_path / "paired.json"),
        patch.object(app_module, "default_collection_folder", return_value=tmp_path / "collection"),
        patch.object(app_module, "default_collection_config_path", return_value=tmp_path / "collection.json"),
        patch.object(app_module, "MacCollector", return_value=fake),
        patch.object(app_module, "start_bridge_server", side_effect=OSError("in use")),
        patch.object(app_module.rumps, "notification"),
    ):
        app = app_module.ScanixMenuBarApp()
    assert app.icon.endswith("collecting.png") and app.title == "2"
    titles = [item.title for item in app.menu.values() if hasattr(item, "title")]
    assert "Finish now (2 pages)" in titles and "Discard collected pages…" in titles


def test_start_up_without_a_collection_folder_runs_the_old_way(tmp_path):
    from scanix500.menubar import app as app_module

    with (
        patch.object(app_module, "load_profiles", return_value=[Profile(name="Default", destination="/tmp/scans")]),
        patch.object(app_module, "default_profiles_path", return_value="/tmp/scanix500-test-profiles.json"),
        patch.object(app_module, "load_bridge_destination", return_value="/tmp/scans"),
        patch.object(app_module, "default_bridge_destination_path", return_value="/tmp/scanix500-test-bridge-destination.txt"),
        patch.object(app_module, "default_pairing_path", return_value=tmp_path / "paired.json"),
        patch.object(app_module, "default_collection_folder", return_value=tmp_path / "collection"),
        patch.object(app_module, "MacCollector", side_effect=OSError("read-only")),
        patch.object(app_module, "start_bridge_server", side_effect=OSError("in use")),
        patch.object(app_module.rumps, "notification"),
    ):
        app = app_module.ScanixMenuBarApp()
    assert app._collector is None
    assert app.icon.endswith("idle.png")
    assert app._start_finish() is False


def _rebuilt(app):
    """Runs the real _rebuild_menu against a real rumps menu; returns its titles."""
    from scanix500.menubar import app as app_module

    app._menu = app_module.rumps.rumps.Menu()
    app.quit_button = None
    app._app_started = False
    app._bridge_server = None
    app.pairing = Mock()
    del app._rebuild_menu  # use the class method
    app._rebuild_menu()
    return [item.title for item in app.menu.values() if hasattr(item, "title")]


def test_menu_has_the_collection_items_only_while_a_collection_is_open():
    app = _collecting_app(pages=3)
    titles = _rebuilt(app)
    assert "Finish now (3 pages)" in titles and "Discard collected pages…" in titles
    status = titles.index("Not listening (scan bridge unavailable)")
    assert titles.index("Finish now (3 pages)") > status
    assert titles.index("Finish now (3 pages)") < titles.index(app.profiles[0].name)

    app = _collecting_app(pages=1)
    assert "Finish now (1 page)" in _rebuilt(app)

    app = _collecting_app(pages=0)
    titles = _rebuilt(app)
    assert not any(str(x).startswith(("Finish now", "Discard collected")) for x in titles)


def test_discard_asks_first_and_only_discards_on_ok(sync):
    from scanix500.menubar import app as app_module

    app = _collecting_app(pages=3)
    with patch.object(app_module.rumps, "alert", return_value=0) as alert:
        app._discard_clicked(None)
    assert alert.call_args.kwargs["title"] == "Discard 3 collected pages?"
    assert alert.call_args.kwargs["ok"] == "Discard"
    assert app._collector.calls == [] and app._collector.page_count == 3

    with patch.object(app_module.rumps, "alert", return_value=1):
        app._discard_clicked(None)
    assert app._collector.calls == [("discard",)]
    assert app.icon.endswith("idle.png") and app.title == ""
    assert app._rebuilds == 1


def test_discard_is_ignored_while_scanning():
    from scanix500.menubar import app as app_module

    app = _collecting_app(pages=3)
    app._scanning = True
    with patch.object(app_module.rumps, "alert") as alert:
        app._discard_clicked(None)
    alert.assert_not_called()
    assert app._collector.calls == []


def test_collecting_state_falls_back_to_the_emoji_and_the_count():
    from scanix500.menubar import app as app_module

    app = _collecting_app(pages=4)
    with patch.object(app_module, "icon_path", return_value=None):
        app._show("collecting")
    assert app.icon is None and app.title == f"{app_module.COLLECTING_TITLE} 4"
