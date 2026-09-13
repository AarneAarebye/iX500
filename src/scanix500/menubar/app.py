# src/scanix500/menubar/app.py
from __future__ import annotations

import threading
from pathlib import Path

import rumps
from AppKit import NSOpenPanel
from Foundation import NSURL
from PyObjCTools import AppHelper

from scanix500.menubar.profiles import (
    Profile,
    add_profile,
    default_profiles_path,
    delete_profile,
    load_profiles,
    replace_profile,
    save_profiles,
)
from scanix500.menubar.runner import ScanResult, notification_title, run_scan

IDLE_TITLE = "📄"
SCANNING_TITLE = "📄…"


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
        self._rebuild_menu()

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
        # initializeStatusBar() — which runs after __init__. Every later
        # _rebuild_menu() call clears it away, so re-add it here. Menu.add is
        # keyed by title and ignores duplicates, so doing this before rumps'
        # own add (during __init__) is harmless.
        if self.quit_button is not None:
            self.menu.add(self.quit_button)

    def _make_scan_handler(self, profile: Profile):
        def handler(_sender):
            if self._scanning:
                return
            self._scanning = True
            self.title = SCANNING_TITLE
            threading.Thread(target=self._run_scan_thread, args=(profile,), daemon=True).start()
        return handler

    def _run_scan_thread(self, profile: Profile):
        # Any exception here would kill this thread silently and strand
        # _scanning=True forever, so convert every failure into a ScanResult.
        try:
            result = run_scan(profile)
        except Exception as e:  # noqa: BLE001 - must never wedge the app
            result = ScanResult(ok=False, partial=False, message=str(e), output_paths=[])
        # rumps.Timer.start() schedules onto NSRunLoop.currentRunLoop(), which
        # from this worker thread is a run loop nothing ever runs — the
        # callback would never fire. AppHelper.callAfter marshals onto the
        # main thread's run loop from any thread.
        AppHelper.callAfter(self._on_scan_complete, result)

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

        # NOTE: rumps.alert's return value convention (1 = the `ok` button,
        # 0 = the `cancel` button) is documented but unverified in this
        # environment — confirm in the manual checklist.
        skip_blank_filter = rumps.alert("Profile", "Skip blank-page filter?", ok="Yes", cancel="No") == 1
        skip_ocr = rumps.alert("Profile", "Skip OCR?", ok="Yes", cancel="No") == 1
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
