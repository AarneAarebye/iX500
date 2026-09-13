# scanix500 menu bar app (Phase 2)

## Background

Phase 1 delivered `scanix500`, a CLI that drives the ScanSnap iX500 directly via SANE, replacing ScanSnap Home. It fully replaces ScanSnap Home's scanning functionality but requires opening a terminal and typing a command. Phase 2, per the original pipeline design's phasing, adds a macOS menu bar app as a thin wrapper around the same CLI — no terminal required for day-to-day use, closer to the original ScanSnap Home experience.

## Goal

A menu bar app that:
- offers one-click scanning via saved **profiles** (name, destination folder, and the CLI's flag choices — e.g. a "Receipts" profile with OCR off vs a "Documents" profile with OCR on), similar to ScanSnap Home's profile concept
- lets profiles be added/edited/deleted from within the app (no hand-editing config files)
- stays responsive while a scan runs (icon indicates in-progress state) and reports success, partial success (multi-feed recovery), or failure via macOS notifications
- auto-launches at login

Non-goals: no redesign or reimplementation of the scanning pipeline itself (Phase 1's CLI is unchanged and remains the single source of truth for capture/filter/OCR/routing logic); no support for triggering a scan from the physical Scan button (that's Phase 3); no packaging as a distributable `.app` bundle (a plain script run via a LaunchAgent is sufficient).

## Architecture

```
┌─────────────────────────────────────────────┐
│  Menu bar app (rumps, new)                   │
│  - Reads profiles.json at launch             │
│  - Menu: one item per profile + Edit Profiles│
│  - Click profile → spawn scanix500 subprocess│
│    in background thread                      │
│  - Icon: idle ⇄ scanning states              │
│  - macOS notification on completion/error    │
└───────────────────┬───────────────────────────┘
                    │ subprocess call (same as Terminal would)
┌───────────────────▼───────────────────────────┐
│  scanix500 CLI (existing, Phase 1, unchanged) │
└─────────────────────────────────────────────────┘
```

The menu bar app calls `scanix500` as a **subprocess**, exactly like running it from Terminal — it does not import `capture.py`/`pdf_builder.py`/etc. directly. This keeps the CLI as the single tested, owned implementation, and lets the two evolve independently. Built with **rumps** (a lightweight Python library purpose-built for macOS menu bar apps), running as a plain script — no `.app` bundle packaging required. Profiles are stored in `~/Library/Application Support/scanix500/profiles.json`.

## Profile data model

Each profile is a JSON object:
```json
{
  "name": "Documents",
  "destination": "/Users/arne/Documents/Scans",
  "skip_blank_filter": false,
  "skip_ocr": false,
  "split_on_blank": false
}
```
`profiles.json` is a list of these. On first launch with no file present, the app seeds one default profile (name "Default", destination `~/Documents/Scans` — created if it doesn't exist, all flags off) so the menu is never empty.

## Profile editing

Sequential native dialogs (rumps' built-in primitives — `rumps.Window` for text entry, `rumps.alert` for yes/no, AppKit's `NSOpenPanel` for folder picking — no custom multi-field form UI):

- **Add Profile**: name → destination folder (native picker) → "Skip blank-page filter?" → "Skip OCR?" → "Split on blank separator sheets?" → written to `profiles.json`, menu rebuilds.
- **Edit Profile**: pick profile from submenu → same five-step walk, pre-filled with current values → overwrites that entry.
- **Delete Profile**: pick profile from submenu → confirm → removed, menu rebuilds. Refuses to delete the last remaining profile.

## Scan execution & feedback

```
Click profile "Documents" in menu
   → disable that menu item (prevent double-click re-entry)
   → icon switches to "scanning" state
   → background thread: subprocess.run(["scanix500", destination, <flags>], capture_output=True)
   → on thread completion (back on main thread via rumps' timer/callback):
        - icon reverts to idle state
        - re-enable the menu item
        - exit code 0 → notification: "Scan complete" + output path(s) (parsed from stdout)
        - exit code 0, "All pages were blank" → notification shows that message instead
        - exit code 1 → notification: "Scan failed" + stderr message
        - multi-feed case (exit 1 but a PDF WAS written) → notification frames this as
          partial success, e.g. "Multi-feed at sheet 3 — partial scan saved to <path>",
          not a bare failure
```

Only one scan runs at a time — while a scan is in flight, all profile menu items are disabled (the physical scanner can't do two things at once).

## Auto-launch at login

A macOS LaunchAgent (`~/Library/LaunchAgents/com.scanix500.menubar.plist`) runs the menu bar script's Python interpreter at login, pointed at the project's venv. `RunAtLoad: true`, `KeepAlive: false` (a crash should be noticed via the icon's absence, not silently respawned in a loop). Installing this plist is a one-time manual step, documented in the README — not automated by `scanix500` itself, since writing LaunchAgent files programmatically is exactly the kind of quiet login-item modification worth keeping manual and visible.

## File structure

New files under the existing `scanix500` package:
- `src/scanix500/menubar/app.py` — the `rumps.App` subclass: menu construction from profiles, click handlers, icon state, notification dispatch.
- `src/scanix500/menubar/profiles.py` — pure logic: load/save/validate `profiles.json`, add/edit/delete operations. No rumps/AppKit imports.
- `src/scanix500/menubar/runner.py` — wraps the `subprocess.run(["scanix500", ...])` call and result parsing (exit code, stdout path(s), stderr message, the "blank batch" and "multi-feed partial" special cases) into a plain data structure. No rumps import.
- New console script `scanix500-menubar = "scanix500.menubar.app:main"` and an optional dependency group `[project.optional-dependencies] menubar = ["rumps"]` (kept optional so headless/CI environments don't need a GUI toolkit).

## Testing

Same split as Phase 1:
- `profiles.py` and `runner.py`: real automated unit tests (JSON round-tripping, validation, subprocess-result parsing against fake `CompletedProcess` objects) — no GUI, no real scanner.
- `app.py` (rumps menu/dialogs/icon/notifications): not automated — awkward and low-value to test programmatically. Verified via a manual checklist (click each profile; add/edit/delete a profile; confirm icon states; confirm notifications for success, blank-batch, error, and multi-feed-partial cases) — same pattern as Phase 1's hardware checklist, for UI instead of hardware.
