# src/scanix500/menubar/profile_form.py
from __future__ import annotations

from dataclasses import replace

import objc
from AppKit import (
    NSApplication,
    NSBackingStoreBuffered,
    NSButton,
    NSButtonTypeSwitch,
    NSMakeRect,
    NSOpenPanel,
    NSPopUpButton,
    NSTextField,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSNotificationCenter, NSObject, NSURL

from scanix500.i18n import t
from scanix500.menubar.profiles import Profile, profile_for_scanner, scanner_choices
from scanix500.scanners import CANOSCAN_8800F_ID, IX500_ID

WINDOW_WIDTH = 440
WINDOW_HEIGHT = 256


def pick_folder(default_path: str) -> str | None:
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseDirectories_(True)
    panel.setCanChooseFiles_(False)
    panel.setCanCreateDirectories_(True)
    panel.setAllowsMultipleSelection_(False)
    panel.setDirectoryURL_(NSURL.fileURLWithPath_(default_path))
    # 1 is NSOpenPanel's documented "OK button" response — the same
    # convention app.py's own folder picker already relied on (confirmed
    # against the installed rumps/PyObjC source during Phase 2's final
    # review).
    if panel.runModal() == 1:
        return str(panel.URL().path())
    return None


class _ProfileFormDelegate(NSObject):
    """Owns the window's widgets and every button/text-change callback.
    Only ever constructed by show_profile_form() below — this is the
    interactive-only half of this feature, with zero automated test
    coverage by design (see this project's existing precedent for live
    AppKit/GUI code in app.py and button_watcher.py)."""

    def initWithDefaultDestination_(self, default_destination: str):
        self = objc.super(_ProfileFormDelegate, self).init()
        if self is None:
            return None
        self.result: str | None = None  # "ok" or "cancel" once the modal ends
        self.destination = default_destination
        self.existing: Profile | None = None  # the profile being edited, if any
        self._scanner_ids = [scanner_id for scanner_id, _ in scanner_choices()]
        self._split_wanted = False  # the checkbox state to restore for the iX500
        self._build_window()
        return self

    def _build_window(self) -> None:
        rect = NSMakeRect(0, 0, WINDOW_WIDTH, WINDOW_HEIGHT)
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        window.setTitle_(t("Profile"))
        window.center()
        self.window = window
        window.setDelegate_(self)
        content = window.contentView()

        name_label = NSTextField.labelWithString_(t("Name:"))
        name_label.setFrame_(NSMakeRect(20, 212, 116, 20))
        content.addSubview_(name_label)

        self.name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(144, 210, 276, 24))
        content.addSubview_(self.name_field)

        scanner_label = NSTextField.labelWithString_(t("Scanner:"))
        scanner_label.setFrame_(NSMakeRect(20, 176, 116, 20))
        content.addSubview_(scanner_label)

        self.scanner_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(144, 172, 276, 28), False
        )
        for _scanner_id, scanner_name in scanner_choices():
            self.scanner_popup.addItemWithTitle_(scanner_name)
        self.scanner_popup.setTarget_(self)
        self.scanner_popup.setAction_("scannerChanged:")
        content.addSubview_(self.scanner_popup)

        dest_label = NSTextField.labelWithString_(t("Destination:"))
        dest_label.setFrame_(NSMakeRect(20, 140, 116, 20))
        content.addSubview_(dest_label)

        self.destination_field = NSTextField.alloc().initWithFrame_(NSMakeRect(144, 138, 168, 24))
        self.destination_field.setEditable_(False)
        self.destination_field.setSelectable_(False)
        content.addSubview_(self.destination_field)

        choose_button = NSButton.alloc().initWithFrame_(NSMakeRect(320, 136, 100, 28))
        choose_button.setTitle_(t("Choose…"))
        choose_button.setTarget_(self)
        choose_button.setAction_("chooseClicked:")
        content.addSubview_(choose_button)

        self.blank_filter_checkbox = self._make_checkbox(t("Use blank-page filter"), 100)
        self.ocr_checkbox = self._make_checkbox(t("Use OCR"), 74)
        self.split_checkbox = self._make_checkbox(t("Split on blank separator sheets"), 48)
        self.split_checkbox.setTarget_(self)
        self.split_checkbox.setAction_("splitClicked:")

        cancel_button = NSButton.alloc().initWithFrame_(NSMakeRect(212, 12, 100, 32))
        cancel_button.setTitle_(t("Cancel"))
        cancel_button.setTarget_(self)
        cancel_button.setAction_("cancelClicked:")
        content.addSubview_(cancel_button)

        self.ok_button = NSButton.alloc().initWithFrame_(NSMakeRect(320, 12, 100, 32))
        self.ok_button.setTitle_(t("OK"))
        self.ok_button.setTarget_(self)
        self.ok_button.setAction_("okClicked:")
        content.addSubview_(self.ok_button)

        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, "nameFieldChanged:", "NSControlTextDidChangeNotification", self.name_field
        )
        self._update_ok_enabled()

    def _make_checkbox(self, title: str, y: int) -> NSButton:
        checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(20, y, 400, 24))
        checkbox.setButtonType_(NSButtonTypeSwitch)
        checkbox.setTitle_(title)
        self.window.contentView().addSubview_(checkbox)
        return checkbox

    def set_initial_values(
        self,
        name: str,
        destination: str,
        skip_blank_filter: bool,
        skip_ocr: bool,
        split_on_blank: bool,
        scanner_id: str = IX500_ID,
    ) -> None:
        self.name_field.setStringValue_(name)
        self.destination_field.setStringValue_(destination)
        self.destination = destination
        # blank_filter_checkbox/ocr_checkbox show "Use X", so their checked
        # state is the inverse of the skip_* fields Profile actually stores.
        self.blank_filter_checkbox.setState_(0 if skip_blank_filter else 1)
        self.ocr_checkbox.setState_(0 if skip_ocr else 1)
        self.split_checkbox.setState_(1 if split_on_blank else 0)
        self._split_wanted = split_on_blank
        if scanner_id in self._scanner_ids:
            self.scanner_popup.selectItemAtIndex_(self._scanner_ids.index(scanner_id))
        self._apply_scanner_state()
        self._update_ok_enabled()

    def _selected_scanner_id(self) -> str:
        index = int(self.scanner_popup.indexOfSelectedItem())
        return self._scanner_ids[index] if 0 <= index < len(self._scanner_ids) else IX500_ID

    def _apply_scanner_state(self) -> None:
        """The 8800F is a flatbed: no separator sheets. Grey out and uncheck
        the split checkbox for it; the iX500 gets the user's last state back."""
        if self._selected_scanner_id() == CANOSCAN_8800F_ID:
            self.split_checkbox.setState_(0)
            self.split_checkbox.setEnabled_(False)
        else:
            self.split_checkbox.setEnabled_(True)
            self.split_checkbox.setState_(1 if self._split_wanted else 0)

    def scannerChanged_(self, _sender) -> None:
        # _split_wanted only changes when the user clicks the (enabled) box
        self._apply_scanner_state()

    def splitClicked_(self, _sender) -> None:
        self._split_wanted = self.split_checkbox.state() == 1

    def _update_ok_enabled(self) -> None:
        self.ok_button.setEnabled_(bool(str(self.name_field.stringValue()).strip()))

    def nameFieldChanged_(self, _notification) -> None:
        self._update_ok_enabled()

    def chooseClicked_(self, _sender) -> None:
        chosen = pick_folder(self.destination)
        if chosen is not None:
            self.destination = chosen
            self.destination_field.setStringValue_(chosen)

    def okClicked_(self, _sender) -> None:
        self.window.makeFirstResponder_(None)
        self.result = "ok"
        NSApplication.sharedApplication().stopModal()

    def cancelClicked_(self, _sender) -> None:
        self.result = "cancel"
        NSApplication.sharedApplication().stopModal()

    def windowShouldClose_(self, _sender) -> bool:
        self.cancelClicked_(_sender)
        return True

    def build_profile(self) -> Profile:
        fields = dict(
            name=str(self.name_field.stringValue()).strip(),
            destination=self.destination,
            skip_blank_filter=self.blank_filter_checkbox.state() == 0,
            skip_ocr=self.ocr_checkbox.state() == 0,
            split_on_blank=self.split_checkbox.state() == 1,
        )
        # Start from the profile being edited so the fields the form does not
        # show (duplex, colour mode, resolution, ...) survive an edit.
        if self.existing is not None:
            result = replace(self.existing, **fields)
        else:
            result = Profile(**fields)
        chosen = self._selected_scanner_id()
        if self.existing is None or chosen != self.existing.scanner:
            # Only a scanner change adjusts the hidden fields; plain edits keep them.
            result = profile_for_scanner(result, chosen)
        return result


def show_profile_form(existing: Profile | None, default_destination: str) -> Profile | None:
    """Builds and runs the profile settings window modally. Returns the
    Profile the user configured, or None if they cancelled."""
    delegate = _ProfileFormDelegate.alloc().initWithDefaultDestination_(default_destination)
    delegate.existing = existing
    if existing is not None:
        delegate.set_initial_values(
            existing.name,
            existing.destination,
            existing.skip_blank_filter,
            existing.skip_ocr,
            existing.split_on_blank,
            existing.scanner,
        )
    else:
        delegate.set_initial_values("", default_destination, False, False, False)

    NSApplication.sharedApplication().runModalForWindow_(delegate.window)
    delegate.window.orderOut_(None)
    NSNotificationCenter.defaultCenter().removeObserver_(delegate)

    if delegate.result != "ok":
        return None
    return delegate.build_profile()
