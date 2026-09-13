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

macOS's system/Homebrew Python is "externally managed" (PEP 668) and
refuses `pip install` outside a virtual environment — plain `pip` may
also not exist on `PATH` at all. Create a venv first:

    brew install sane-backends ocrmypdf tesseract
    python3 -m venv .venv
    .venv/bin/pip install python-sane
    .venv/bin/pip install -e ".[dev]"

From here on, replace bare `pip`/`pytest`/`scanix500` with
`.venv/bin/pip`/`.venv/bin/pytest`/`.venv/bin/scanix500` — or run
`source .venv/bin/activate` once per terminal session to use the bare
commands for that session.

## Run

    .venv/bin/scanix500 ~/Documents/Scans
    .venv/bin/scanix500 ~/Documents/Scans --split-on-blank
    .venv/bin/scanix500 ~/Documents/Scans --skip-ocr
    .venv/bin/scanix500 ~/Documents/Scans --skip-blank-filter

## Automated tests

    .venv/bin/pytest

24 tests cover all pure logic (capture pairing/multi-feed/empty-ADF/odd-frame
handling, blank detection and splitting, PDF assembly, page DPI geometry and
OCR fallback, output path naming, and CLI wiring including destination-folder
creation, all-blank batches and multi-feed partial-batch processing) using
fakes and mocks — no scanner hardware required.

## Manual hardware checklist

Run these by hand against the real iX500 after any change to `capture.py` or
`pdf_builder.py` — hardware behavior isn't mocked:

- [x] `scanimage -L` and/or `sane.get_devices()` shows the iX500 via the
      `fujitsu` backend. **Verified 2026-09-13** against a real iX500:
      device string `fujitsu:ScanSnap iX500:1203900`, `get_devices()` returns
      `('fujitsu:ScanSnap iX500:1203900', 'FUJITSU', 'ScanSnap iX500',
      'scanner')` — the existing `"fujitsu" in d[1].lower()` match works
      as-is. **Gotcha found:** a third-party scanning app (VueScan) running
      in the background held the USB device open and caused
      `connect_fd: could not open device` — quit any other scanner app first.
- [x] Load a double-sided stack, run `scanix500 <dest>`: resulting PDF has
      pages in correct front/back/front/back order. **Verified 2026-09-13**
      with real documents — capture, blank-page filtering, and OCR all
      worked correctly end-to-end against physical hardware.
- [x] Default scan settings are A4 @ 300 DPI. **Verified 2026-09-13**:
      `PySaneDevice` sets `resolution=300`, `page_width=210`,
      `page_height=297` (mm) — confirmed setting `page_width`/`page_height`
      auto-syncs the scan area (`br_x`/`br_y`) on real hardware, and a real
      scan produced a PDF page at exactly 210.0x297.0mm.
- [x] **Fixed 2026-09-13, hardware-verified:** scans come out in color, not
      black & white. `PySaneDevice` never set the `mode` option, so it
      silently used the backend's own default — confirmed to be `Lineart`
      (pure 1-bit black & white, not even grayscale) via
      `scanimage --help -d <device>`. Now explicitly sets `mode="Color"`.
      Re-verified with a real double-sided color document: output pages
      came out in full color.
- [ ] Load a stack with one intentionally blank backside: resulting PDF
      has that blank page removed.
- [ ] Load a stack with a fully blank separator sheet in the middle, run
      with `--split-on-blank`: two separate PDFs are produced.
- [x] **Fixed 2026-09-13, hardware-verified:** output PDF page size matches
      the original document. Real captures came out ~3x oversized (25.5x33in
      instead of 8.5x11in) because `python-sane`'s `snap()`/`multi_scan()`
      build the PIL image via `Image.frombuffer()` and never set
      `.info["dpi"]` — confirmed by reading `sane.py`'s source. `PySaneDevice`
      now stamps `frame.info["dpi"] = (self._dev.resolution,) * 2` in
      `read_frame()` so `pdf_builder.py`'s existing DPI-aware page sizing
      gets real data instead of always falling back to its 200 DPI default.
      Re-verified after the fix: a 600 DPI real scan produced an
      8.49x11.00in page.
- [x] Multi-feed detection redesigned 2026-09-13 based on real-hardware
      investigation. Findings:
      - The correct sensor is `omr_df` ("OMR or double feed detected"), not
        `double_feed_detected` as originally guessed — confirmed via
        `sane.get_options()` and `scanimage -A -d <device>` (it's hidden from
        plain `scanimage --help`). `multi_feed_detected()` now polls this.
      - Detection is OFF by default and must be explicitly enabled.
        `df-thickness`/`df-skew` start `SANE_CAP_INACTIVE` until `df-action`
        is set to a non-default value — confirmed by reading back capability
        flags before/after. `PySaneDevice.__init__` now sets
        `df_action="Stop"`, `df_thickness=True`, `df_skew=True`.
        `df-length` is deliberately left disabled (would false-positive on
        any page shorter than a full sheet, e.g. receipts).
      - The compiled backend contains the string `"Document feeder jammed"`
        (the standard `SANE_STATUS_JAMMED` message) alongside double-feed
        debug strings, strongly suggesting a real double-feed can abort the
        read itself (raising an exception) rather than only setting a flag
        afterward. `capture_pages()` now catches any non-`StopIteration`
        exception from `read_frame()` and converts it to `MultiFeedError`
        with pages captured so far preserved, instead of crashing uncaught.
      - **Still unconfirmed:** three deliberate attempts to trigger a real
        double-feed (stacked sheets face-to-face, offset/angled, and a
        folded sheet fed folded-edge-first) all failed — the ADF's
        separation mechanism successfully peeled every attempt apart
        cleanly, `omr_df` never moved, no exception was raised. Re-verified
        a normal scan afterward still works correctly with detection
        enabled (no false positives). The exact trigger conditions and
        failure signature remain to be observed from a real accidental
        double-feed during normal use — update this note when one occurs.
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

Uses the same venv as above:

    .venv/bin/pip install -e ".[menubar]"

### Run manually

    .venv/bin/scanix500-menubar

A menu bar icon (📄) appears with one item per saved profile, plus
Add/Edit/Delete Profile submenus. Profiles are stored at
`~/Library/Application Support/scanix500/profiles.json` and seeded with a
single "Default" profile on first run.

### Auto-launch at login

1. Find the installed script's full path: `cd /path/to/iX500 && pwd -P` then append `/.venv/bin/scanix500-menubar` (or `which scanix500-menubar` if you've activated the venv in your current shell)
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
