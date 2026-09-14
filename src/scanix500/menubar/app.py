# src/scanix500/menubar/app.py
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import rumps
from AppKit import NSOpenPanel
from Foundation import NSURL
from PyObjCTools import AppHelper

from scanix500.menubar.bridge import ScanBusyError, start_bridge_server
from scanix500.menubar.button_watcher import button_pressed, resolve_device_name
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
from scanix500.menubar.runner import ScanResult, notification_title, run_scan

IDLE_TITLE = "📄"
SCANNING_TITLE = "📄…"

# While no device name is resolved yet, only retry resolve_device_name()
# every Nth poll tick (20 * 1.5s ≈ 30s). That call does sane.init() +
# sane.get_devices(), which probes every backend and commonly blocks for
# seconds on the main thread — orders of magnitude more expensive than the
# ~5-20ms open/close that button_pressed() does, which is what the 1.5s
# poll interval was actually budgeted for. Retrying it every tick with no
# scanner present freezes the menu bar UI roughly continuously.
RESOLVE_RETRY_TICKS = 20


def _pick_folder(default_path: str) -> str | None:
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseDirectories_(True)
    panel.setCanChooseFiles_(False)
    panel.setCanCreateDirectories_(True)
    panel.setAllowsMultipleSelection_(False)
    panel.setDirectoryURL_(NSURL.fileURLWithPath_(default_path))
    # NOTE: 1 is NSOpenPanel's documented "OK button" response. Verify this
    # against the installed AppKit/PyObjC version in the manual checklist —
    # it cannot be exercised without a live GUI session.
    if panel.runModal() == 1:
        return str(panel.URL().path())
    return None


class ScanixMenuBarApp(rumps.App):
    def __init__(self):
        super().__init__("scanix500", title=IDLE_TITLE)
        self.profiles_path = default_profiles_path()
        self.profiles = load_profiles(self.profiles_path)
        self._scanning = False
        # See _rebuild_menu's comment on self._app_started.
        self._app_started = False
        self._rebuild_menu()
        # Resolved once and cached indefinitely — confirmed empirically (unplug/
        # replug test against real hardware, 2026-09-14) that this backend's
        # device name is based on the unit's persistent serial number, not a
        # USB bus path, so it stays stable across reconnects. No invalidation
        # needed.
        self._button_device_name: str | None = None
        self._ticks_since_resolve_attempt = 0
        self._button_timer = rumps.Timer(self._poll_button, 1.5)
        self._button_timer.start()
        # Embedded HTTP bridge (see bridge.py) -- lets an external caller
        # (Dossiary's browser JS) trigger a scan by profile name over
        # local HTTP. self.profiles is read fresh on every bridge request
        # (via the lambda), not snapshotted here, so Add/Edit/Delete
        # Profile while the bridge is running is picked up immediately.
        # The bridge is an optional enhancement, not core functionality --
        # a taken port (OSError) or a non-numeric SCANIX500_BRIDGE_PORT
        # (ValueError) must never take the whole menu bar app down with it,
        # since a launchd-launched process has nowhere useful for an
        # uncaught traceback to go.
        try:
            self._bridge_server = start_bridge_server(lambda: self.profiles, self)
        except (OSError, ValueError) as e:
            self._bridge_server = None
            rumps.notification(title="Scan bridge unavailable", subtitle="", message=str(e))

    def _rebuild_menu(self):
        self.menu.clear()
        for profile in self.profiles:
            self.menu.add(rumps.MenuItem(profile.name, callback=self._make_scan_handler(profile)))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Add Profile…", callback=self._add_profile))

        edit_menu = rumps.MenuItem("Edit Profile…")
        for profile in self.profiles:
            edit_menu.add(rumps.MenuItem(profile.name, callback=self._make_edit_handler(profile.name)))
        self.menu.add(edit_menu)

        delete_menu = rumps.MenuItem("Delete Profile…")
        for profile in self.profiles:
            delete_menu.add(rumps.MenuItem(profile.name, callback=self._make_delete_handler(profile.name)))
        self.menu.add(delete_menu)

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
        _poll_button's hardware-button path) both ignore the return value
        and never pass on_done -- unchanged behavior for them. trigger()
        (below) is the one new caller that uses both."""
        if self._scanning:
            return False
        self._scanning = True
        self.title = SCANNING_TITLE
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
        # Don't touch the device at all while a scan is running: run_scan
        # spawns the scanix500 CLI as a subprocess that holds the device open
        # for the whole scan, so polling it would mean repeated open attempts
        # against a device another process is actively streaming from — for no
        # benefit, since _start_scan discards a press detected mid-scan anyway.
        if self._scanning:
            return
        if self._button_device_name is None:
            if self._ticks_since_resolve_attempt % RESOLVE_RETRY_TICKS == 0:
                self._button_device_name = resolve_device_name()
            self._ticks_since_resolve_attempt += 1
            if self._button_device_name is None:
                return
        if not button_pressed(self._button_device_name):
            return
        profile = find_profile(self.profiles, HARDWARE_BUTTON_PROFILE_NAME)
        if profile is not None:
            self._start_scan(profile)

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
        self.title = IDLE_TITLE
        rumps.notification(title=notification_title(result), subtitle="", message=result.message)

    def _prompt_profile_fields(self, existing: Profile | None) -> Profile | None:
        name_response = rumps.Window(
            "Profile name:",
            "Profile",
            default_text=existing.name if existing else "",
            cancel="Cancel",
        ).run()
        if not name_response.clicked or not name_response.text:
            return None
        name = name_response.text

        default_destination = existing.destination if existing else str(Path.home() / "Documents" / "Scans")
        destination = _pick_folder(default_destination)
        if destination is None:
            return None

        # rumps.alert's return value convention (1 = the `ok` button, 0 =
        # the `cancel` button) was confirmed against the installed rumps
        # source during Phase 2's final review.
        #
        # Questions are phrased as "Use X?" (positive framing) rather than
        # "Skip X?", so a "Yes" answer always means "turn the feature on" —
        # the skip_* fields are still what Profile/the CLI expect, so the
        # answer is inverted right here rather than changing that contract.
        use_blank_filter = rumps.alert("Profile", "Use blank-page filter?", ok="Yes", cancel="No") == 1
        skip_blank_filter = not use_blank_filter
        use_ocr = rumps.alert("Profile", "Use OCR?", ok="Yes", cancel="No") == 1
        skip_ocr = not use_ocr
        split_on_blank = rumps.alert(
            "Profile", "Split on blank separator sheets?", ok="Yes", cancel="No"
        ) == 1

        return Profile(name, destination, skip_blank_filter, skip_ocr, split_on_blank)

    def _add_profile(self, _sender):
        if self._scanning:
            return
        profile = self._prompt_profile_fields(None)
        if profile is None:
            return
        try:
            self.profiles = add_profile(self.profiles, profile)
        except ValueError as e:
            rumps.alert("Add Profile", str(e))
            return
        save_profiles(self.profiles_path, self.profiles)
        self._rebuild_menu()

    def _make_edit_handler(self, name: str):
        def handler(_sender):
            if self._scanning:
                return
            current = next(p for p in self.profiles if p.name == name)
            updated = self._prompt_profile_fields(current)
            if updated is None:
                return
            try:
                self.profiles = replace_profile(self.profiles, name, updated)
            except ValueError as e:
                rumps.alert("Edit Profile", str(e))
                return
            save_profiles(self.profiles_path, self.profiles)
            self._rebuild_menu()
        return handler

    def _make_delete_handler(self, name: str):
        def handler(_sender):
            if self._scanning:
                return
            confirmed = rumps.alert("Delete Profile", f'Delete profile "{name}"?', ok="Delete", cancel="Cancel") == 1
            if not confirmed:
                return
            try:
                self.profiles = delete_profile(self.profiles, name)
            except ValueError as e:
                rumps.alert("Delete Profile", str(e))
                return
            save_profiles(self.profiles_path, self.profiles)
            self._rebuild_menu()
        return handler


def main():
    ScanixMenuBarApp().run()


if __name__ == "__main__":
    main()
