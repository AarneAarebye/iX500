# scanix500

A vendor-independent scan pipeline for the ScanSnap iX500, driving it
directly via SANE instead of ScanSnap Home.

## Why

Ricoh/PFU's ScanSnap Home no longer activates this iX500 (red-error badge,
greyed-out Scan button) — a software-lifecycle decision on their side, not
a hardware fault. Rather than depend on Ricoh's shrinking support window,
this drives the scanner directly via the open-source SANE `fujitsu`
backend, which has had funded, maintained iX500 support for years.

See [`docs/superpowers/specs/2026-09-13-scanix500-pipeline-design.md`](docs/superpowers/specs/2026-09-13-scanix500-pipeline-design.md)
for the full design and [`docs/superpowers/plans/2026-09-13-scanix500-cli-pipeline.md`](docs/superpowers/plans/2026-09-13-scanix500-cli-pipeline.md)
for the implementation plan.

## Status

**Phase 1 (CLI pipeline) complete and tested.** The `scanix500` command-line tool is
fully implemented with capture, blank detection, splitting, PDF assembly, and OCR
integration. Automated tests cover all pure logic. Manual hardware tests are documented
below and must be run against the physical iX500 to verify scanner behavior.

Phase 2 (menu bar app) and Phase 3 (physical button trigger) are future work per the
[implementation plan](docs/superpowers/plans/2026-09-13-scanix500-cli-pipeline.md).

## Install

    brew install sane-backends ocrmypdf tesseract
    pip install python-sane
    pip install -e ".[dev]"

## Run

    scanix500 ~/Documents/Scans
    scanix500 ~/Documents/Scans --split-on-blank
    scanix500 ~/Documents/Scans --skip-ocr
    scanix500 ~/Documents/Scans --skip-blank-filter

## Automated tests

    pytest

24 tests cover all pure logic (capture pairing/multi-feed/empty-ADF/odd-frame
handling, blank detection and splitting, PDF assembly, page DPI geometry and
OCR fallback, output path naming, and CLI wiring including destination-folder
creation, all-blank batches and multi-feed partial-batch processing) using
fakes and mocks — no scanner hardware required.

## Manual hardware checklist

Run these by hand against the real iX500 after any change to `capture.py` or
`pdf_builder.py` — hardware behavior isn't mocked:

- [ ] `scanimage -L` and/or `sane.get_devices()` shows the iX500 via the
      `fujitsu` backend. If not, `PySaneDevice.__init__`'s device matching
      needs adjusting. `sane.get_devices()` returns `(name, vendor, model,
      type)` tuples, and the match currently accepts a device whose vendor
      (`d[1]`) contains `"fujitsu"` or whose model (`d[2]`) contains
      `"ix500"`. Print the real tuples from `sane.get_devices()` and widen or
      correct those substrings to whatever this unit actually reports.
- [ ] Load a 5-page double-sided stack, run `scanix500 <dest>`: resulting
      PDF has 10 pages in correct front/back/front/back order.
- [ ] Load a stack with one intentionally blank backside: resulting PDF
      has that blank page removed.
- [ ] Load a stack with a fully blank separator sheet in the middle, run
      with `--split-on-blank`: two separate PDFs are produced.
- [ ] Deliberately feed two sheets stuck together: `scanix500` reports a
      multi-feed error and exits non-zero, and the pages captured before
      the jam are still written to a PDF. Confirm `PySaneDevice`'s
      `multi_feed_detected()` reads the right backend option: it currently
      reads `self._dev.double_feed_detected` and returns `False` if that
      attribute doesn't exist, so a wrong name fails silently (multi-feeds
      would never be reported). Run `sane.open(...).get_options()` (or
      `scanimage -A -d <device>`) against this unit and check for a
      double-feed/multi-feed option; python-sane exposes SANE option names
      with `-` replaced by `_`. If the option is named differently on this
      firmware/backend version, update the attribute name in
      `multi_feed_detected()` to match.
- [ ] Resulting PDF text is selectable/searchable (OCR ran) unless
      `--skip-ocr` was passed.
- [ ] Open the resulting PDF and check its physical page size (Preview's
      Inspector, or `pdfinfo`): it should match the original document
      (e.g. A4 / Letter), not an oversized page. This verifies the scan's
      DPI is being carried into the PDF page geometry.
- [ ] Scan a stack to the bottom without intervening: the ADF terminates
      cleanly — the batch ends by itself with no error and no hang once the
      last sheet has fed.

## Phase 2: Menu bar app

### Install

    pip install -e ".[menubar]"

### Run manually

    scanix500-menubar

A menu bar icon (📄) appears with one item per saved profile, plus
Add/Edit/Delete Profile submenus. Profiles are stored at
`~/Library/Application Support/scanix500/profiles.json` and seeded with a
single "Default" profile on first run.

### Auto-launch at login

1. Find the installed script's full path: `which scanix500-menubar`
2. Copy `packaging/com.scanix500.menubar.plist` to `~/Library/LaunchAgents/`
3. Edit the copied plist's `ProgramArguments` entry to the path from step 1
4. Edit the copied plist's `EnvironmentVariables` → `PATH` value so its first
   entry is the same venv `bin/` directory used in `ProgramArguments` (e.g.
   `/path/to/your/venv/bin:/usr/bin:/bin`). launchd otherwise starts the app
   with a minimal `PATH` that doesn't include the venv, so the `scanix500`
   CLI can't be found. (`runner.py` also resolves `scanix500` next to the
   running interpreter, so this is defense in depth.)
5. `launchctl load ~/Library/LaunchAgents/com.scanix500.menubar.plist`

If the menu bar icon doesn't appear after logging in, check
`/tmp/scanix500-menubar.log` (the plist's `StandardOutPath`/`StandardErrorPath`)
for errors.

To stop it from auto-launching: `launchctl unload ~/Library/LaunchAgents/com.scanix500.menubar.plist`
then remove the plist file.

### Manual UI checklist

Run these by hand — `app.py` has no automated tests, since it's live
GUI/AppKit code:

- [ ] The first notification may require approving Python/the app in
      System Settings → Notifications — macOS attributes notifications from
      this venv's interpreter to "Python" the first time.
- [ ] Clicking a profile in the menu triggers a scan; the icon changes
      to the "scanning" state and reverts when done.
- [ ] A successful scan shows a "Scan complete" notification naming the
      output path.
- [ ] An all-blank batch shows a notification with that message, no crash.
- [ ] A scanner-not-found or other hard failure shows a "Scan failed"
      notification with a clear message.
- [ ] A multi-feed jam (see Phase 1's manual hardware checklist) shows a
      "Scan partially completed" notification distinct from a bare failure,
      naming the partial output path.
- [ ] Add Profile: name entry → folder picker → three yes/no prompts →
      new profile appears in the menu and in `profiles.json`.
- [ ] Edit Profile: existing values are used as the starting point in the
      name/destination prompts; changes are saved and reflected in the menu.
- [ ] Delete Profile: confirmation prompt appears; deleting the last
      remaining profile is refused with a clear alert instead of silently
      failing or leaving an empty menu.
- [ ] Clicking a profile, or Add/Edit/Delete, while a scan is already in
      progress does nothing (no double-scan, no crash).
- [ ] Adding a profile whose name already exists is refused with a clear
      alert instead of creating a duplicate menu row.
- [ ] After adding/editing/deleting a profile, the menu still has a working
      Quit item (the menu is rebuilt from scratch on each change).
- [ ] Quit and relaunch `scanix500-menubar`: profiles persist correctly
      from `profiles.json`.
