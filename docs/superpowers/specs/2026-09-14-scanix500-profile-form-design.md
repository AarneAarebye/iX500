# scanix500 menu bar app: real profile settings window

## Background

Phase 2's profile Add/Edit flow (`app.py`'s `_prompt_profile_fields`) uses a sequence of separate `rumps` primitives — a name-entry window, a native folder picker, then three sequential yes/no alerts ("Use blank-page filter?", "Use OCR?", "Split on blank separator sheets?") — because `rumps` itself has no multi-field form widget, and building a real one was explicitly deferred at Phase 2's brainstorming in favor of shipping something simple first.

Having used it, the sequential-popup flow is clunky, and it has a real gap: the three yes/no prompts never show or default to a profile's *current* settings when editing, so editing a profile means re-answering all three from scratch every time with no memory of what's already set.

## Goal

Replace the whole sequence with a single settings window containing all fields at once — name, destination folder, and the three checkboxes — for both Add and Edit. Editing pre-fills every field, including the three checkboxes, from the profile's actual current values.

Non-goals: no change to duplicate-name handling (stays exactly as implemented today — `add_profile`/`replace_profile` raise, `app.py` catches and shows the existing `rumps.alert`); no change to Delete Profile's confirmation flow; no new UI toolkit dependency (built on the `PyObjC`/AppKit that `rumps` already pulls in).

## Architecture

A new module, `src/scanix500/menubar/profile_form.py`, builds and runs a real `NSWindow` as a modal form (the same `runModal()` pattern already used for the existing folder picker), replacing the current sequence of separate popups with one window:

```
┌─────────────────────────────────────────┐
│  Profile                                 │
│                                           │
│  Name:        [___________________]      │
│                                           │
│  Destination: [/Users/.../Scans] [Choose…]│
│                                           │
│  ☑ Use blank-page filter                 │
│  ☑ Use OCR                               │
│  ☐ Split on blank separator sheets       │
│                                           │
│                        [Cancel]  [OK]    │
└─────────────────────────────────────────┘
```

`app.py`'s `_add_profile`/`_make_edit_handler` call this instead of today's `_prompt_profile_fields`; everything downstream (save/rebuild menu, duplicate-name error handling) is unchanged. The "Choose…" button reuses the existing `_pick_folder`/`NSOpenPanel` logic, which moves into this new file alongside the rest of the form (it's tightly coupled to it and no longer used from `app.py` directly). OK is disabled until the name field is non-empty, via an `NSTextField` delegate watching for text changes — the one piece of live validation this design adds; everything else (duplicate names) is still checked on submit via the existing exception path.

## Integration & Data Flow

```
app.py: _add_profile(_sender)
   → profile_form.show_profile_form(existing=None, default_destination=...)
        → builds window, shows it modally
        → OK enabled only once name is non-empty (live, via delegate)
        → "Choose…" opens NSOpenPanel, updates the destination field's displayed text
        → on OK: reads all field values, returns a Profile
        → on Cancel (or window closed): returns None
   → if None: abort, unchanged
   → if Profile: add_profile(...) / save_profiles(...) / _rebuild_menu()
        (unchanged — duplicate-name ValueError still caught and shown via
        rumps.alert exactly as today)

app.py: _make_edit_handler(name) → same show_profile_form(existing=<that Profile>, ...)
        → every field, including the three checkboxes, pre-filled from `existing`
```

## Components

- **`src/scanix500/menubar/profile_form.py`** (new): `show_profile_form(existing: Profile | None, default_destination: str) -> Profile | None` is the only function `app.py` calls. Internally holds a small AppKit delegate object (subclassing `NSObject` via PyObjC) that watches the name field for text changes to toggle the OK button's enabled state, and handles the OK/Cancel button actions to stop the modal loop and report which was clicked. `_pick_folder` moves here from `app.py`.
- **`app.py`**: `_prompt_profile_fields` is removed; `_add_profile` and `_make_edit_handler` call `show_profile_form` instead. No other logic changes.

## Testing

`profile_form.py` gets no automated tests — it's live AppKit UI code, the same category as the rest of `app.py` and `button_watcher.py` in this project (verified manually, not mocked). Manual checklist:
- Add Profile shows all fields empty, OK disabled.
- Typing a name enables OK.
- Choose… opens the folder picker and updates the displayed destination path.
- Edit Profile pre-fills every field, including all three checkboxes, from the profile's actual current values.
- Cancel discards changes without saving.
- Submitting a name that collides with an existing profile still shows the existing duplicate-name error alert (unchanged behavior).
