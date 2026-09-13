# scanix500 physical Scan-button trigger (Phase 3)

## Background

Phase 1's original pipeline design flagged the physical Scan button as a "research spike, not guaranteed" — feasibility on macOS was unconfirmed, and the fallback plan was to try a Linux-oriented tool (`scanbd`/`insaned`) that turned out to have no clean macOS packaging path.

This spec is written after directly investigating the real hardware. The findings change the picture substantially:

- `scanimage -A -d <device>` (all-options mode; the button state is hidden from the plain `--help` output) reveals a `scan` sensor option ("Scan button") that reflects the physical button's pressed state.
- Reading it requires bypassing a naming collision: `python-sane`'s `SaneDev` class defines its own `scan()` method (for triggering a scan), which shadows attribute access to the SANE option of the same name. The raw value is still reachable via `dev.opt['scan'].index` + `dev.dev.get_option(index)`.
- The raw value only reflects a fresh press if the option descriptor list is re-fetched (`dev.get_options()`) immediately before reading it — a stale/cached descriptor set does not pick up the hardware-side change.
- Once these two things are accounted for, a real button press was confirmed to produce a clean `0 → 1 → 0` transition, reliably, across multiple presses.
- Opening and closing the device per poll cycle (rather than holding it open continuously) was measured at ~5-20ms per cycle on real hardware — cheap enough to poll every 1-2 seconds while leaving the device available to other applications almost all the time.

No external daemon (`scanbd`, `insaned`) is needed. A small polling loop using `python-sane` directly, integrated into the existing Phase 2 menu bar app, is sufficient.

## Goal

Pressing the iX500's physical Scan button triggers a scan using a dedicated, auto-seeded "Hardware Button" profile — reusing Phase 2's existing profile/run/notification machinery entirely. No separate process, no new UI beyond what Phase 2 already has (the profile is editable/deletable like any other).

Non-goals: no way to select which profile the button triggers from the hardware itself (it always uses the profile named "Hardware Button"); no support for running the button-watcher without the menu bar app also running; no attempt to make the polling interval user-configurable in this phase.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Menu bar app (existing, Phase 2)            │
│  + rumps.Timer, ~1.5s interval, MAIN THREAD  │
│    - calls button_pressed(device_name)       │
│    - open→check 'scan' option→close (cheap,  │
│      ~5-20ms measured) — releases the device │
│      between polls so other apps can use it  │
│    - if pressed and not already scanning:    │
│      trigger the same scan-launch path as a  │
│      menu click, using the "Hardware Button" │
│      profile                                 │
└───────────────────┬───────────────────────────┘
                    │ reuses existing scan-thread + run_scan machinery
┌───────────────────▼───────────────────────────┐
│  scanix500 CLI (Phase 1, unchanged)           │
└─────────────────────────────────────────────────┘
```

The poll runs on a `rumps.Timer` started normally on the main thread during `__init__` — its intended usage pattern — rather than a background thread. This sidesteps the exact cross-thread `Timer` pitfall Phase 2's final review caught and fixed (a `Timer` started off the main thread never fires against the installed rumps library): polling is cheap enough to run inline on the main thread without blocking the UI. The actual scan (slow) still runs on a background thread exactly as it already does for menu clicks — only the trigger changes, not the execution path.

## Components

**`src/scanix500/menubar/button_watcher.py`** (new file):
- `button_pressed(device_name: str) -> bool` — opens the SANE device by name, re-fetches option descriptors (confirmed necessary), reads the `scan` option's raw value via `dev.dev.get_option(index)` (bypassing `python-sane`'s own `scan()` method), closes the device, and returns whether it was pressed. Any exception (device busy, unplugged, transient failure) is caught and treated as "not pressed."
- Hardware-dependent, same category as `PySaneDevice` — no automated test, verified via the manual checklist.

**`src/scanix500/capture.py`** (extraction, DRY improvement): the device-matching logic currently inline in `PySaneDevice.__init__` is pulled into a pure function, `find_fujitsu_device(devices: list[tuple]) -> str | None`, taking the `(name, vendor, model, type)` tuples `sane.get_devices()` returns. Both `PySaneDevice` and `button_watcher.py` call it. This is the one piece of this feature that's fully unit-testable.

**`src/scanix500/menubar/app.py`** (modified): `ScanixMenuBarApp.__init__` starts a `rumps.Timer(self._poll_button, 1.5)`. The callback calls `button_watcher.button_pressed(...)`; on a press, if `not self._scanning` and a profile named `"Hardware Button"` exists in `self.profiles`, it calls the same scan-launching logic already used by profile-menu clicks (refactored into a shared `_start_scan(profile)` method so both paths use one implementation).

## Profile seeding & migration

`profiles.py`'s `load_profiles()` currently seeds a single `"Default"` profile only when `profiles.json` doesn't exist yet. For "Hardware Button" to work out of the box — including on an **existing** installation that already has a `profiles.json` without it — seeding runs as a light migration on every load, not just on fresh-install:

```
load_profiles(path):
    if path doesn't exist:
        seed ["Default", "Hardware Button"], persist, return
    load profiles from file
    if no profile named "Hardware Button" exists:
        append a default "Hardware Button" profile, persist, return updated list
    return profiles as loaded
```

Upgrading from Phase 2 to Phase 3 gets the profile added automatically the next time the menu bar app starts. Afterward it's an ordinary profile: editable, deletable, renameable via the existing Add/Edit/Delete UI. If deleted or renamed, the button becomes a silent no-op until a profile with that exact name exists again.

## Error handling

- **Device not found / busy**: `button_pressed()` returns `False` silently — no notification, no log spam. The next cycle tries again. This matters because another app, or `scanix500` itself mid-scan, may legitimately hold the device at any given poll moment.
- **Press detected while already scanning**: the existing `_scanning` guard applies identically to menu clicks and button presses — a no-op.
- **No "Hardware Button" profile found**: silent no-op, consistent with the project's existing no-op-over-error-spam pattern.
- **Scanner physically absent at startup**: the poller returns `False` every cycle until it's plugged back in — no crash, no special-cased reconnect logic, since each poll attempt is cheap and self-contained.

## Testing

- `find_fujitsu_device()`: real unit tests, pure function over plain tuples — backfills test coverage Phase 1 never had for this exact matching logic.
- `button_watcher.button_pressed()`: hardware-dependent, no automated test — verified via a manual checklist entry (press the button, confirm a scan launches using the "Hardware Button" profile; confirm no crash when the scanner is unplugged; confirm the profile-migration seeding adds "Hardware Button" to an existing `profiles.json` on next launch).
- `app.py`'s `rumps.Timer` wiring: no automated test (existing GUI exemption), manual checklist only.
