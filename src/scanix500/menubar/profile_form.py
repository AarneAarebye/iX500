# src/scanix500/menubar/profile_form.py
from __future__ import annotations

from AppKit import (
    NSApplication,
    NSBackingStoreBuffered,
    NSButton,
    NSButtonTypeSwitch,
    NSMakeRect,
    NSOpenPanel,
    NSTextField,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSNotificationCenter, NSObject, NSURL

from scanix500.menubar.profiles import Profile

WINDOW_WIDTH = 420
WINDOW_HEIGHT = 220


def _pick_folder(default_path: str) -> str | None:
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
        self = super().init()
        if self is None:
            return None
        self.result: str | None = None  # "ok" or "cancel" once the modal ends
        self.destination = default_destination
        self._build_window()
        return self

    def _build_window(self) -> None:
        rect = NSMakeRect(0, 0, WINDOW_WIDTH, WINDOW_HEIGHT)
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        window.setTitle_("Profile")
        window.center()
        self.window = window
        content = window.contentView()

        name_label = NSTextField.labelWithString_("Name:")
        name_label.setFrame_(NSMakeRect(20, 176, 80, 20))
        content.addSubview_(name_label)

        self.name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(100, 174, 300, 24))
        content.addSubview_(self.name_field)

        dest_label = NSTextField.labelWithString_("Destination:")
        dest_label.setFrame_(NSMakeRect(20, 140, 80, 20))
        content.addSubview_(dest_label)

        self.destination_field = NSTextField.alloc().initWithFrame_(NSMakeRect(100, 138, 220, 24))
        self.destination_field.setEditable_(False)
        self.destination_field.setSelectable_(False)
        content.addSubview_(self.destination_field)

        choose_button = NSButton.alloc().initWithFrame_(NSMakeRect(330, 136, 70, 28))
        choose_button.setTitle_("Choose…")
        choose_button.setTarget_(self)
        choose_button.setAction_("chooseClicked:")
        content.addSubview_(choose_button)

        self.blank_filter_checkbox = self._make_checkbox("Use blank-page filter", 100)
        self.ocr_checkbox = self._make_checkbox("Use OCR", 74)
        self.split_checkbox = self._make_checkbox("Split on blank separator sheets", 48)

        cancel_button = NSButton.alloc().initWithFrame_(NSMakeRect(220, 12, 90, 32))
        cancel_button.setTitle_("Cancel")
        cancel_button.setTarget_(self)
        cancel_button.setAction_("cancelClicked:")
        content.addSubview_(cancel_button)

        self.ok_button = NSButton.alloc().initWithFrame_(NSMakeRect(316, 12, 90, 32))
        self.ok_button.setTitle_("OK")
        self.ok_button.setTarget_(self)
        self.ok_button.setAction_("okClicked:")
        content.addSubview_(self.ok_button)

        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, "nameFieldChanged:", "NSControlTextDidChangeNotification", self.name_field
        )
        self._update_ok_enabled()

    def _make_checkbox(self, title: str, y: int) -> NSButton:
        checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(20, y, 380, 24))
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
    ) -> None:
        self.name_field.setStringValue_(name)
        self.destination_field.setStringValue_(destination)
        self.destination = destination
        # blank_filter_checkbox/ocr_checkbox show "Use X", so their checked
        # state is the inverse of the skip_* fields Profile actually stores.
        self.blank_filter_checkbox.setState_(0 if skip_blank_filter else 1)
        self.ocr_checkbox.setState_(0 if skip_ocr else 1)
        self.split_checkbox.setState_(1 if split_on_blank else 0)
        self._update_ok_enabled()

    def _update_ok_enabled(self) -> None:
        self.ok_button.setEnabled_(bool(str(self.name_field.stringValue()).strip()))

    def nameFieldChanged_(self, _notification) -> None:
        self._update_ok_enabled()

    def chooseClicked_(self, _sender) -> None:
        chosen = _pick_folder(self.destination)
        if chosen is not None:
            self.destination = chosen
            self.destination_field.setStringValue_(chosen)

    def okClicked_(self, _sender) -> None:
        self.result = "ok"
        NSApplication.sharedApplication().stopModal()

    def cancelClicked_(self, _sender) -> None:
        self.result = "cancel"
        NSApplication.sharedApplication().stopModal()

    def build_profile(self) -> Profile:
        return Profile(
            name=str(self.name_field.stringValue()),
            destination=self.destination,
            skip_blank_filter=self.blank_filter_checkbox.state() == 0,
            skip_ocr=self.ocr_checkbox.state() == 0,
            split_on_blank=self.split_checkbox.state() == 1,
        )


def show_profile_form(existing: Profile | None, default_destination: str) -> Profile | None:
    """Builds and runs the profile settings window modally. Returns the
    Profile the user configured, or None if they cancelled."""
    delegate = _ProfileFormDelegate.alloc().initWithDefaultDestination_(default_destination)
    if existing is not None:
        delegate.set_initial_values(
            existing.name,
            existing.destination,
            existing.skip_blank_filter,
            existing.skip_ocr,
            existing.split_on_blank,
        )
    else:
        delegate.set_initial_values("", default_destination, False, False, False)

    NSApplication.sharedApplication().runModalForWindow_(delegate.window)
    delegate.window.orderOut_(None)
    NSNotificationCenter.defaultCenter().removeObserver_(delegate)

    if delegate.result != "ok":
        return None
    return delegate.build_profile()
