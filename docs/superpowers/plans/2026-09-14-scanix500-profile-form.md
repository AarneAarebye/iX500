# scanix500 Profile Settings Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the menu bar app's sequential name/folder/three-yes-no-prompt flow for adding and editing a scan profile with a single AppKit form window showing all fields at once, pre-filled with current values on Edit.

**Architecture:** A new module, `profile_form.py`, builds a plain `NSWindow` (name field, destination field + "Choose…" button reusing the existing folder-picker pattern, three checkboxes, OK/Cancel) and runs it modally via `NSApplication.sharedApplication().runModalForWindow_(...)`, the same blocking-dialog style already used elsewhere in this app. `app.py` calls it in place of today's `_prompt_profile_fields`; everything downstream (save, duplicate-name error handling, menu rebuild) is unchanged.

**Tech Stack:** Python 3.11+, PyObjC/AppKit (already a dependency via `rumps`), `pytest`.

## Global Constraints

- `profile_form.py` gets NO automated tests — it's live AppKit UI code, the same category as the rest of `app.py` and `button_watcher.py` in this project (verified via a manual checklist, not mocked).
- OK is disabled until the name field is non-empty; this is the one piece of live validation this feature adds. Duplicate-name checking is NOT duplicated here — it stays exactly where it already is (`add_profile`/`replace_profile` raise `ValueError`, `app.py` catches it and shows the existing `rumps.alert`, unchanged).
- Editing a profile must pre-fill every field, including all three checkboxes, from that profile's actual current values — today's sequential prompts never do this for the checkboxes, and fixing that is a stated goal of this plan.
- Every AppKit class/constant used (`NSButtonTypeSwitch`, `NSWindowStyleMaskTitled`, etc.) is written using modern (macOS 10.12+) naming and is a best-effort choice pending verification against the actually-installed PyObjC framework, in the same spirit as this project's existing hardware/GUI code (e.g. `_pick_folder`'s `runModal() == 1`, `PySaneDevice`'s SANE option names) — if any name is wrong on the real system, the fix is confined to that one line, verified via the manual checklist.
- No new dependency is introduced — `AppKit`/`Foundation`/PyObjC are already available via `rumps`.

---

### Task 1: Build the profile settings window (`profile_form.py`)

**Files:**
- Create: `src/scanix500/menubar/profile_form.py`

**Interfaces:**
- Produces: `scanix500.menubar.profile_form.show_profile_form(existing: Profile | None, default_destination: str) -> Profile | None` — used by Task 2 (`app.py`).

This file is live AppKit UI code with NO automated tests, per the Global Constraints above. There is no TDD cycle for this task. Implement it in one pass, verify it imports cleanly, and commit. Full interactive verification happens via the manual checklist (Task 3).

- [ ] **Step 1: Implement profile_form.py**

```python
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
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `.venv/bin/python -c "import scanix500.menubar.profile_form"`
Expected: no output, exit code 0

- [ ] **Step 3: Run the full existing test suite to confirm nothing broke**

Run: `.venv/bin/pytest -v`
Expected: all previously-passing tests still pass (this task adds no new automated tests)

- [ ] **Step 4: Commit**

```bash
git add src/scanix500/menubar/profile_form.py
git commit -m "Add profile_form: a single AppKit window replacing the sequential profile-editing prompts"
```

---

### Task 2: Wire the new form into the menu bar app

**Files:**
- Modify: `src/scanix500/menubar/app.py`

**Interfaces:**
- Consumes: `scanix500.menubar.profile_form.show_profile_form` (Task 1).

This file is GUI code with NO automated tests, per the Global Constraints — same as Task 1. Implement in one pass, verify the import, run the full suite, commit.

- [ ] **Step 1: Replace _prompt_profile_fields's callers and remove the old method**

In `src/scanix500/menubar/app.py`:

1. Add the import near the top, alongside the existing `scanix500.menubar` imports:

```python
from scanix500.menubar.profile_form import show_profile_form
```

2. Delete the entire `_prompt_profile_fields` method (currently defined right after `_on_scan_complete`) — its body is superseded by `show_profile_form`.

3. Delete the module-level `_pick_folder` function and its `NSOpenPanel`/`NSURL` imports at the top of the file (`from AppKit import NSOpenPanel` and `from Foundation import NSURL`) — this logic now lives in `profile_form.py`, and nothing else in `app.py` uses it.

4. Update `_add_profile` to call `show_profile_form` directly instead of `self._prompt_profile_fields`:

```python
    def _add_profile(self, _sender):
        if self._scanning:
            return
        profile = show_profile_form(None, str(Path.home() / "Documents" / "Scans"))
        if profile is None:
            return
        try:
            self.profiles = add_profile(self.profiles, profile)
        except ValueError as e:
            rumps.alert("Add Profile", str(e))
            return
        save_profiles(self.profiles_path, self.profiles)
        self._rebuild_menu()
```

5. Update `_make_edit_handler` to call `show_profile_form` directly:

```python
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
                rumps.alert("Edit Profile", str(e))
                return
            save_profiles(self.profiles_path, self.profiles)
            self._rebuild_menu()
        return handler
```

`Path` (from `pathlib`) is already imported at the top of `app.py` for this exact default-destination expression — no new import needed for it.

- [ ] **Step 2: Verify the module imports cleanly**

Run: `.venv/bin/python -c "import scanix500.menubar.app"`
Expected: no output, exit code 0

- [ ] **Step 3: Run the full test suite to confirm nothing broke**

Run: `.venv/bin/pytest -v`
Expected: all previously-passing tests still pass (this task adds no new automated tests)

- [ ] **Step 4: Commit**

```bash
git add src/scanix500/menubar/app.py
git commit -m "Use the new profile settings window for Add/Edit Profile"
```

---

### Task 3: Manual verification checklist

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the stale Add/Edit checklist items with ones matching the new window**

In `README.md`'s Phase 2 "Manual UI checklist" section, find these two existing items:

```markdown
- [ ] Add Profile: name entry → folder picker → three yes/no prompts →
      new profile appears in the menu and in `profiles.json`.
- [ ] Edit Profile: existing values are used as the starting point in the
      name/destination prompts; changes are saved and reflected in the menu.
```

Replace them with:

```markdown
- [ ] Add Profile: opens a single window with an empty name field, OK
      initially disabled. Typing a name enables OK. "Choose…" opens the
      folder picker and updates the displayed destination path. Clicking
      OK with the three checkboxes in any combination creates a profile
      matching them, which appears in the menu and in `profiles.json`.
- [ ] Edit Profile: the window opens pre-filled with that profile's
      current name, destination, and all three checkboxes matching its
      actual current settings (not blank/default) — this is the gap the
      2026-09-14 profile-settings-window change fixed; the old sequential
      prompts never showed the current checkbox values on Edit. Changing
      fields and clicking OK saves the changes and updates the menu.
- [ ] Cancel (in either Add or Edit): closes the window without creating
      or modifying any profile.
- [ ] Add or Edit a profile using a name that collides with an existing
      profile: still shows the existing duplicate-name error alert, same
      as before this change.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Update manual checklist for the new profile settings window"
```
