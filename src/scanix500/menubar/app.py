# src/scanix500/menubar/app.py
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import rumps
from PyObjCTools import AppHelper

from scanix500.i18n import choose_language, macos_preferred_languages, set_language, t
from scanix500.menubar.bridge import RealBackend, ScanBusyError, start_bridge_server
from scanix500.menubar.ocr_jobs import OcrJobQueue, default_ocr_jobs_path
from scanix500.menubar.bridge_settings import (
    default_bridge_destination_path,
    load_bridge_destination,
    save_bridge_destination,
)
from scanix500.menubar.pairing import PairingStore, default_pairing_path
from scanix500 import __version__
from scanix500.menubar.button_watcher import BUTTON_READERS, FINISH, resolve_device_name, sane_exit
from scanix500.menubar.collecting import (
    FinishOutcome,
    MacCollector,
    PageOutcome,
    default_collection_config_path,
    default_collection_folder,
    load_collect_timeout,
)
from scanix500.menubar.profile_form import pick_folder, show_profile_form
from scanix500.menubar.profiles import (
    HARDWARE_BUTTON_PROFILE_NAME,
    Profile,
    add_profile,
    default_profiles_path,
    delete_profile,
    find_profile,
    load_profiles,
    replace_profile,
    save_profiles,
)
from scanix500.menubar.runner import ScanResult, display_message, notification_title, run_scan
from scanix500.scanners import CANOSCAN_8800F_ID, get_scanner, usb_ids

logger = logging.getLogger(__name__)

IDLE_TITLE = "📄"
SCANNING_TITLE = "📄…"
COLLECTING_TITLE = "📄"  # the page count follows it when the icon file is missing
COLLECT_TICK_SECONDS = 15

# Template icons (black on transparent; macOS tints them for light/dark),
# drawn by scripts/make_menubar_icons.py. The emoji titles above are the
# fallback when a file is missing or cannot be loaded.
ICON_DIR = Path(__file__).parent / "icons"


def icon_path(name: str) -> str | None:
    path = ICON_DIR / f"{name}.png"
    return str(path) if path.is_file() else None

# The Scan buttons are polled every 1.5 s, but nothing slow runs on the main
# thread: _poll_button only starts one background read (_button_read_thread)
# and returns, and a press comes back via AppHelper.callAfter. The reads are
# slow -- opening the 8800F takes 0.6-0.8 s (pixma re-runs its device search,
# including the BJNP network probe, on every open), and sane.get_devices()
# takes ~4 s on this Mac -- so they must never run on the main thread.
# A scanner whose USB id usb_ids() doesn't list is never opened. While a
# plugged-in scanner's device name is not resolved yet, resolve_device_name()
# (sane.init() + get_devices()) is only retried every Nth read (20 * 1.5 s
# ≈ 30 s), so an unresolvable scanner doesn't keep SANE busy.
RESOLVE_RETRY_TICKS = 20


class ScanixMenuBarApp(rumps.App):
    def __init__(self):
        # template=True must be given here: rumps applies it to every later
        # `self.icon = ...` assignment, too. With an icon the title is None
        # (rumps falls back on the app name only if there is neither a title
        # nor an image).
        idle_icon = icon_path("idle")
        super().__init__("scanix500", title=None if idle_icon else IDLE_TITLE, icon=idle_icon,
                         template=True, quit_button=t("Quit"))
        self.profiles_path = default_profiles_path()
        self.profiles = load_profiles(self.profiles_path)
        self.bridge_destination_path = default_bridge_destination_path()
        self.bridge_destination = load_bridge_destination(self.bridge_destination_path)
        # Browsers paired with the bridge (see pairing.py): a scan request
        # needs a token from "Pair a Browser…".
        self.pairing = PairingStore(default_pairing_path())
        self._scanning = False
        # Collecting 8800F button pages into one PDF (see collecting.py). If
        # its folder cannot be made, the app runs without collecting -- the
        # old behaviour -- and every use below guards for None.
        try:
            folder = default_collection_folder()
            self._collector = MacCollector(
                folder, folder.parent / "collection-tmp",
                timeout_seconds=load_collect_timeout(default_collection_config_path()) * 60)
        except Exception:  # noqa: BLE001 - collecting is optional
            logger.exception("collecting is unavailable")
            self._collector = None
        # See _rebuild_menu's comment on self._app_started.
        self._app_started = False
        self._rebuild_menu()
        # Scanner id -> SANE device name, cached while the scanner's USB id is
        # present and forgotten as soon as it is unplugged (see _read_buttons),
        # so a re-plugged scanner is resolved again. Only the button-read
        # worker touches it, and only one worker runs at a time.
        self._button_devices: dict[str, str | None] = {}
        self._ticks_since_resolve_attempt = 0
        self._button_reading = False  # a button-read worker is running (main thread only)
        self._button_timer = rumps.Timer(self._poll_button, 1.5)
        self._button_timer.start()
        self._collect_timer = rumps.Timer(self._collect_tick, COLLECT_TICK_SECONDS)
        self._collect_timer.start()
        self._show(self._resting_state())  # a recovered open collection shows at start-up
        # Embedded HTTP bridge (see bridge.py) -- lets an external caller
        # (Dossiary's browser JS) trigger a scan with its own parameters
        # over local HTTP. self.bridge_destination is read fresh on every
        # bridge request (via the lambda), not snapshotted here, so
        # changing "Set Bridge Scan Folder…" while the bridge is running
        # is picked up immediately. The bridge is an optional enhancement,
        # not core functionality -- a taken port (OSError) or a
        # non-numeric SCANIX500_BRIDGE_PORT (ValueError) must never take
        # the whole menu bar app down with it, since a launchd-launched
        # process has nowhere useful for an uncaught traceback to go.
        # The OCR queue is optional: if its folder can't be made, the bridge
        # still scans and simply doesn't offer background OCR.
        try:
            self._ocr_jobs = OcrJobQueue(default_ocr_jobs_path())
        except OSError as e:
            self._ocr_jobs = None
            rumps.notification(title=t("Background OCR unavailable"), subtitle="", message=str(e))
        try:
            backend = RealBackend(lambda: self.bridge_destination, self,
                                  ocr_jobs=self._ocr_jobs if self._ocr_jobs is not None else False)
            self._bridge_server = start_bridge_server(lambda: self.bridge_destination, self, self.pairing,
                                                      backend=backend)
            if self._ocr_jobs is not None:
                self._ocr_jobs.start()  # now, so unfinished jobs resume after a restart
        except (OSError, ValueError) as e:
            self._bridge_server = None
            rumps.notification(title=t("Scan bridge unavailable"), subtitle="", message=str(e))
        self._bridge_status_item.title = self._bridge_status_title()

    def _show(self, state: str) -> None:
        """Shows the "idle", "scanning" or "collecting" state: the template
        icon with an empty title, or with the number of collected pages while
        a collection is open ("collecting" and "scanning" both), or the emoji
        title when the icon file is missing or cannot be loaded. Main thread
        only (it touches the status item)."""
        count = self._collected_count() if state in ("collecting", "scanning") else ""
        base = {"scanning": SCANNING_TITLE, "collecting": COLLECTING_TITLE}.get(state, IDLE_TITLE)
        emoji = f"{base} {count}" if count else base
        path = icon_path(state)
        if path is not None:
            try:
                self.icon = path
                self.title = count
                return
            except Exception:
                pass
        self.icon = None
        self.title = emoji

    def _collected_count(self) -> str:
        """The page count to show next to the icon; "" without an open collection."""
        collector = getattr(self, "_collector", None)
        try:
            return str(collector.page_count) if collector is not None and collector.is_open else ""
        except Exception:  # noqa: BLE001 - a title must never break the app
            return ""

    def _resting_state(self) -> str:
        collector = getattr(self, "_collector", None)
        return "collecting" if collector is not None and collector.is_open else "idle"

    def _bridge_status_title(self) -> str:
        server = getattr(self, "_bridge_server", None)
        if server is None:
            return t("Not listening (scan bridge unavailable)")
        host, port = server.server_address[:2]
        return t("Listening on {address}", address=f"{host}:{port}")

    def _rebuild_menu(self):
        self.menu.clear()
        # Greyed-out header (no callback): the app's name and where its bridge
        # listens, like Dossiary Scan Helper's menu. The bridge starts after
        # the first rebuild, so __init__ refreshes the second item's title.
        self.menu.add(rumps.MenuItem(f"scanix500 {__version__}"))
        self._bridge_status_item = rumps.MenuItem(self._bridge_status_title())
        self.menu.add(self._bridge_status_item)
        collector = getattr(self, "_collector", None)
        if collector is not None and collector.is_open:
            self.menu.add(rumps.separator)
            count = collector.page_count
            finish_title = (t("Finish now (1 page)") if count == 1
                            else t("Finish now ({count} pages)", count=count))
            self.menu.add(rumps.MenuItem(finish_title, callback=self._finish_clicked))
            self.menu.add(rumps.MenuItem(t("Discard collected pages…"), callback=self._discard_clicked))
        self.menu.add(rumps.separator)
        for profile in self.profiles:
            self.menu.add(rumps.MenuItem(profile.name, callback=self._make_scan_handler(profile)))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem(t("Add Profile…"), callback=self._add_profile))

        edit_menu = rumps.MenuItem(t("Edit Profile…"))
        for profile in self.profiles:
            edit_menu.add(rumps.MenuItem(profile.name, callback=self._make_edit_handler(profile.name)))
        self.menu.add(edit_menu)

        delete_menu = rumps.MenuItem(t("Delete Profile…"))
        for profile in self.profiles:
            delete_menu.add(rumps.MenuItem(profile.name, callback=self._make_delete_handler(profile.name)))
        self.menu.add(delete_menu)

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem(t("Set Bridge Scan Folder…"), callback=self._set_bridge_destination))
        self.menu.add(rumps.MenuItem(t("Pair a Browser…"), callback=self._pair_browser))
        self.menu.add(rumps.MenuItem(t("Forget Paired Browsers…"), callback=self._forget_paired_browsers))

        # rumps appends the Quit item itself, but only once, inside
        # initializeStatusBar() — which runs at the end of App.run(), after
        # __init__ (and this first _rebuild_menu call) has already
        # completed. Every LATER _rebuild_menu() call (from Add/Edit/Delete
        # Profile, once the app is already running) clears the Quit item
        # away, so it must be re-added then.
        #
        # It must NOT be added here on the very first call (during
        # __init__), even though rumps.Menu's own bookkeeping is keyed by
        # title and would silently ignore a duplicate key: the underlying
        # AppKit NSMenuItem object cannot belong to two NSMenus at once, and
        # initializeStatusBar() unconditionally does its own
        # mainmenu.add(quit_button) later. Adding it here too — confirmed by
        # actually running the app — makes that later add raise
        # NSInternalInconsistencyException ("Item to be inserted into menu
        # already is in another menu"), crashing the app on every launch.
        if self._app_started and self.quit_button is not None:
            self.menu.add(self.quit_button)
        self._app_started = True

    def _start_scan(self, profile: Profile, on_done: Callable[[ScanResult], None] | None = None) -> bool:
        """Returns True if a scan was actually started, False if one was
        already in progress (in which case on_done is never called). The
        two pre-existing callers (_make_scan_handler's menu-click handler,
        _on_buttons_read's hardware-button path) both ignore the return value
        and never pass on_done -- unchanged behavior for them. trigger()
        (below) is the one new caller that uses both."""
        if self._scanning:
            return False
        self._scanning = True
        self._show("scanning")
        threading.Thread(target=self._run_scan_thread, args=(profile, on_done), daemon=True).start()
        return True

    def _execute_scan(self, profile: Profile) -> ScanResult:
        # Any exception here must never propagate -- _run_scan_thread below
        # would otherwise strand _scanning=True forever, since nothing
        # downstream would ever reset it. Pure logic, no rumps/AppKit
        # dependency -- see tests/menubar/test_app.py.
        try:
            return run_scan(profile)
        except Exception as e:  # noqa: BLE001 - must never wedge the app
            return ScanResult(ok=False, partial=False, message=str(e), output_paths=[])

    def _make_scan_handler(self, profile: Profile):
        def handler(_sender):
            self._start_scan(profile)
        return handler

    def _poll_button(self, _sender):
        """rumps timer callback on the main thread: never does blocking work
        itself, only starts a button-read worker if none runs and no scan does.

        Don't touch any device while a scan is running: run_scan spawns the
        scanix500 CLI as a subprocess that holds the device open for the
        whole scan, so polling would mean repeated open attempts against a
        device another process is actively streaming from -- for no
        benefit, since _start_scan discards a press detected mid-scan anyway."""
        if self._scanning or self._button_reading:
            return
        retry = self._ticks_since_resolve_attempt % RESOLVE_RETRY_TICKS == 0
        self._ticks_since_resolve_attempt += 1
        self._button_reading = True
        threading.Thread(target=self._button_read_thread, args=(retry,), daemon=True).start()

    def _button_read_thread(self, retry: bool):
        hit = None
        try:
            hit = self._read_buttons(retry, usb_ids())
        except Exception:  # noqa: BLE001 - must always report back, or polling stops for good
            hit = None
        finally:
            # A SANE that stays initialised never learns about a scanner plugged in (or
            # power-cycled) later; start from scratch on the next poll.
            sane_exit()
            AppHelper.callAfter(self._on_buttons_read, hit)

    def _on_buttons_read(self, hit: tuple[str, dict] | None):
        """Main thread: the worker is done; a press starts a scan through the
        busy-guarded _start_scan, like a menu click."""
        self._button_reading = False
        if hit is None:
            return
        scanner_id, overrides = hit
        profile = find_profile(self.profiles, HARDWARE_BUTTON_PROFILE_NAME)
        if self._collecting_profile(scanner_id, profile):
            if overrides == FINISH:
                self._start_finish()
            else:
                self._start_page_scan(replace(profile, scanner=scanner_id), overrides)
            return
        if overrides == FINISH:  # only the 8800F collecting into a PDF gives FINISH a meaning
            return
        if profile is not None:
            self._start_scan(replace(profile, scanner=scanner_id, **overrides))

    def _collecting_profile(self, scanner_id: str, profile: Profile | None) -> bool:
        """The 8800F's buttons collect pages while the Hardware Button profile
        makes PDFs and collecting is available."""
        return (getattr(self, "_collector", None) is not None and profile is not None
                and scanner_id == CANOSCAN_8800F_ID and profile.format == "pdf")

    def _start_page_scan(self, profile: Profile, overrides: dict) -> bool:
        """Scans one button page into the open collection on a worker thread.
        Busy-guarded like _start_scan; a press during a scan is ignored."""
        if self._scanning:
            return False
        self._scanning = True
        self._show("scanning")
        threading.Thread(target=self._page_scan_thread, args=(profile, overrides), daemon=True).start()
        return True

    def _safe_page_count(self) -> int:
        try:
            return self._collector.page_count
        except Exception:  # noqa: BLE001 - the fallback must not raise
            return 0

    def _page_scan_thread(self, profile: Profile, overrides: dict):
        try:
            outcome = self._collector.scan_page(profile, overrides)
        except Exception as e:  # noqa: BLE001 - must never leave _scanning stuck
            outcome = PageOutcome(False, self._safe_page_count(), False, str(e))
        AppHelper.callAfter(self._on_page_scanned, outcome)

    def _on_page_scanned(self, outcome: PageOutcome):
        self._scanning = False
        if not outcome.ok:
            rumps.notification(title=t("Scan failed"), subtitle="", message=display_message(outcome.message))
        elif outcome.first_page:
            rumps.notification(title="scanix500", subtitle="",
                               message=t("Collecting pages. Press FINISH when you are done."))
        self._show(self._resting_state())
        self._rebuild_menu()

    def _start_finish(self) -> bool:
        """Merges (and OCRs) the collected pages on a worker thread, under the
        same busy guard as a scan."""
        if self._scanning or getattr(self, "_collector", None) is None or not self._collector.is_open:
            return False
        self._scanning = True
        self._show("scanning")
        threading.Thread(target=self._finish_thread, daemon=True).start()
        return True

    def _finish_thread(self):
        try:
            outcome = self._collector.finish()
        except Exception as e:  # noqa: BLE001 - must never leave _scanning stuck
            outcome = FinishOutcome(None, self._safe_page_count(), False, str(e))
        AppHelper.callAfter(self._on_finished, outcome)

    def _on_finished(self, outcome: FinishOutcome):
        self._scanning = False
        if outcome.path is None:
            rumps.notification(title=t("Scan failed"), subtitle="", message=display_message(outcome.message))
        else:
            if outcome.pages == 1:
                message = t("1 page scanned: {name}", name=outcome.path.name)
            else:
                message = t("{count} pages scanned: {name}", count=outcome.pages, name=outcome.path.name)
            if not outcome.searchable and outcome.message:  # OCR failed; the PDF is kept
                message += "\n" + t("The scan is finished, but the text could not be recognised.")
            rumps.notification(title=t("Scan complete"), subtitle="", message=message)
        self._show(self._resting_state())
        self._rebuild_menu()

    def _collect_tick(self, _sender):
        """rumps timer on the main thread: finishes a collection that has been
        idle for the timeout (expired() is False while anything runs)."""
        collector = getattr(self, "_collector", None)
        if collector is not None and not self._scanning and collector.expired():
            self._start_finish()

    def _finish_clicked(self, _sender):
        self._start_finish()

    def _discard_clicked(self, _sender):
        collector = getattr(self, "_collector", None)
        if self._scanning or collector is None or not collector.is_open:
            return
        count = collector.page_count
        title = t("Discard 1 collected page?") if count == 1 else t("Discard {count} collected pages?", count=count)
        answer = rumps.alert(title=title, message=t("The pages cannot be restored."),
                             ok=t("Discard"), cancel=t("Cancel"))
        if answer != 1 or self._scanning or not collector.is_open:
            return  # a scan may have started while the dialog was open
        collector.discard()
        self._show(self._resting_state())
        self._rebuild_menu()

    def _read_buttons(self, retry: bool, present: set[tuple[int, int]]) -> tuple[str, dict] | None:
        """Reads every plugged-in scanner's buttons (blocking; worker thread).
        Returns (scanner id, Profile overrides) for the first pressed scan
        button, or None. A scanner whose USB id isn't in `present` is never
        touched and its cached device name is forgotten; an unresolved one is
        only resolved when `retry` is set (see RESOLVE_RETRY_TICKS)."""
        for scanner_id, read_button in BUTTON_READERS.items():
            known = get_scanner(scanner_id)
            if known is None or known.usb_id not in present:
                self._button_devices.pop(scanner_id, None)
                continue
            if self._scanning:  # a scan started meanwhile: leave the device to it
                return None
            device_name = self._button_devices.get(scanner_id)
            if device_name is None:
                if not retry:
                    continue
                device_name = self._button_devices[scanner_id] = resolve_device_name(scanner_id)
                if device_name is None:
                    continue
            overrides = read_button(device_name)
            if overrides is not None:
                return scanner_id, overrides
        return None

    def _run_scan_thread(self, profile: Profile, on_done: Callable[[ScanResult], None] | None = None):
        result = self._execute_scan(profile)
        try:
            # rumps.Timer.start() schedules onto NSRunLoop.currentRunLoop(),
            # which from this worker thread is a run loop nothing ever runs
            # — the callback would never fire. AppHelper.callAfter marshals
            # onto the main thread's run loop from any thread.
            #
            # Scheduled before on_done(result) runs below, and on_done must
            # stay in a finally so it always fires -- see trigger()'s own
            # docstring for why this exact ordering (schedule
            # _on_scan_complete, then unblock the waiting bridge thread)
            # matters, not just that on_done eventually gets called.
            AppHelper.callAfter(self._on_scan_complete, result)
        finally:
            if on_done is not None:
                on_done(result)

    def trigger(self, profile: Profile) -> ScanResult:
        """ScanTrigger implementation (see bridge.py) -- called from an HTTP
        request-handler thread (ThreadingHTTPServer spawns one per
        request), never the main thread. Must never touch self.title or
        call rumps.notification directly from here (AppKit calls are only
        safe on the main thread), so the actual check-and-set/scan-start
        happens inside _start_scan(), called via AppHelper.callAfter on
        the main thread -- the exact same function (not a re-implementation
        of its guard) that a menu click or the hardware button already
        calls, so a concurrent bridge request and hardware-button press
        can't both pass the busy check before either sets self._scanning.
        Blocks the calling thread until the scan resolves (or the
        busy-guard fires), so the bridge's HTTP response reflects the real
        ScanResult, not just "started".

        Correctness note: it's safe for this HTTP response to be returned
        before self._scanning is reset back to False only because
        AppHelper.callAfter is FIFO on the main run loop, and
        _run_scan_thread schedules _on_scan_complete (via callAfter) BEFORE
        it calls on_done -- so by the time on_done() unblocks this thread,
        _on_scan_complete is already queued ahead of anything a client's
        immediate follow-up request could trigger. If that ordering in
        _run_scan_thread were ever reversed, an immediate follow-up bridge
        request could race _on_scan_complete's self._scanning = False and
        see a spurious 409 -- don't reorder those two lines there."""
        done = threading.Event()
        result_box: list[ScanResult | None] = [None]
        started_box: list[bool] = [False]

        def on_done(result: ScanResult):
            result_box[0] = result
            done.set()

        def on_main_thread():
            try:
                started_box[0] = self._start_scan(profile, on_done=on_done)
            except Exception:  # noqa: BLE001 - must never strand the waiting HTTP thread
                started_box[0] = False
            if not started_box[0]:
                done.set()

        AppHelper.callAfter(on_main_thread)
        done.wait()
        if not started_box[0]:
            raise ScanBusyError()
        return result_box[0]

    def _on_scan_complete(self, result: ScanResult):
        self._scanning = False
        self._show(self._resting_state())
        rumps.notification(title=notification_title(result), subtitle="", message=display_message(result.message))

    def _pair_browser(self, _sender):
        """Shows a fresh pairing code for Dossiary to enter. The code stays
        valid for 2 minutes (5 attempts) whether or not the alert is still
        open -- the bridge keeps serving on its own threads meanwhile."""
        code = self.pairing.open_window()
        rumps.alert(
            title=t("Pair a Browser"),
            message=t("Enter this code in Dossiary:\n\n{code}\n\nIt's valid for 2 minutes.", code=f"{code[:3]} {code[3:]}"),
            ok=t("OK"),
        )

    def _forget_paired_browsers(self, _sender):
        count = self.pairing.paired_count()
        answer = rumps.alert(
            title=t("Forget Paired Browsers"),
            message=t("Forget all paired browsers ({count})? Dossiary will ask for a new pairing code before its next scan.", count=count),
            ok=t("Forget"),
            cancel=t("Cancel"),
        )
        if answer == 1:
            self.pairing.forget_all()

    def _set_bridge_destination(self, _sender):
        if self._scanning:
            return
        chosen = pick_folder(self.bridge_destination)
        if chosen is None:
            return
        self.bridge_destination = chosen
        save_bridge_destination(self.bridge_destination_path, chosen)

    def _add_profile(self, _sender):
        if self._scanning:
            return
        profile = show_profile_form(None, str(Path.home() / "Documents" / "Scans"))
        if profile is None:
            return
        try:
            self.profiles = add_profile(self.profiles, profile)
        except ValueError as e:
            rumps.alert(t("Add Profile"), str(e), ok=t("OK"))
            return
        save_profiles(self.profiles_path, self.profiles)
        self._rebuild_menu()

    def _make_edit_handler(self, name: str):
        def handler(_sender):
            if self._scanning:
                return
            current = next(p for p in self.profiles if p.name == name)
            updated = show_profile_form(current, current.destination)
            if updated is None:
                return
            try:
                self.profiles = replace_profile(self.profiles, name, updated)
            except ValueError as e:
                rumps.alert(t("Edit Profile"), str(e), ok=t("OK"))
                return
            save_profiles(self.profiles_path, self.profiles)
            self._rebuild_menu()
        return handler

    def _make_delete_handler(self, name: str):
        def handler(_sender):
            if self._scanning:
                return
            confirmed = rumps.alert(t("Delete Profile"), t("Delete profile “{name}”?", name=name), ok=t("Delete"), cancel=t("Cancel")) == 1
            if not confirmed:
                return
            try:
                self.profiles = delete_profile(self.profiles, name)
            except ValueError as e:
                rumps.alert(t("Delete Profile"), str(e), ok=t("OK"))
                return
            save_profiles(self.profiles_path, self.profiles)
            self._rebuild_menu()
        return handler


def main():
    set_language(choose_language(os.environ, macos_preferred_languages()))
    ScanixMenuBarApp().run()


if __name__ == "__main__":
    main()
