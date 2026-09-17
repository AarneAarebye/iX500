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

**All four phases are complete.**

- **Phase 1 — CLI pipeline.** The `scanix500` command-line tool: ADF duplex capture,
  blank-page detection, splitting on separator sheets, PDF assembly, and OCR.
- **Phase 2 — Menu bar app.** `scanix500-menubar`: a macOS menu bar icon with one
  item per saved profile and Add/Edit/Delete Profile management.
- **Phase 3 — Physical button trigger.** Pressing the Scan button on the iX500 itself
  starts a scan using the "Hardware Button" profile.
- **Phase 4 — HTTP bridge.** A localhost-only HTTP endpoint built into the menu bar
  app so an external caller (Dossiary) can trigger a parameterized scan and receive
  the scanned file(s) directly — see "Phase 4: HTTP bridge" below.

Automated tests cover all pure logic. Manual hardware and UI tests are documented in the
checklists below and must be run against the physical iX500 — hardware and GUI behavior
is deliberately not mocked.

## Install

macOS's system/Homebrew Python is "externally managed" (PEP 668) and
refuses `pip install` outside a virtual environment — plain `pip` may
also not exist on `PATH` at all. Create a venv first:

    brew install sane-backends ocrmypdf tesseract tesseract-lang
    python3 -m venv .venv
    CPATH="$(brew --prefix sane-backends)/include" LIBRARY_PATH="$(brew --prefix sane-backends)/lib" .venv/bin/pip install python-sane
    .venv/bin/pip install -e ".[dev]"

`python-sane` compiles a C extension against `sane-backends`' headers
(`sane/sane.h`), and pip has no way to find them on its own — a plain
`.venv/bin/pip install python-sane` fails with
`fatal error: 'sane/sane.h' file not found` (confirmed on real hardware
setup). The `CPATH`/`LIBRARY_PATH` exports above point the compiler at
Homebrew's copy; only needed for this one install command.

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

60 tests cover all pure logic (capture pairing/multi-feed/empty-ADF/odd-frame
handling, SANE device matching, blank detection and splitting, PDF assembly,
page DPI geometry and OCR fallback, output path naming, CLI wiring including
destination-folder creation, all-blank batches and multi-feed partial-batch
processing, and the menu bar app's profile storage — seeding, one-time
Hardware Button migration, lookup — plus scan-output parsing and executable
resolution) using fakes and mocks — no scanner hardware required.

Live hardware code (`button_watcher.py`) and live GUI code (`app.py`) have no
automated tests by design; they're covered by the manual checklists below.

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
- [x] Load a stack with one intentionally blank backside: resulting PDF
      has that blank page removed.
- [x] **Fixed 2026-09-17, hardware-verified:** `--split-on-blank` never
      split on a real blank separator sheet — `is_blank()`'s pixel cutoff
      (`value < 250`) was too strict for this scanner's actual output. A
      real scanned blank sheet (300 DPI, Color) has 35-40% of its pixels
      in the 240-249 range from normal vignetting/paper-texture noise,
      putting it nowhere near the 2% area threshold; confirmed by dumping
      every raw captured page and computing non-background ratios at
      several cutoffs. Lowering the cutoff to `value < 240` drops the
      same real blank sheet to ~1% while every real content page in the
      same test batch stayed above 3% — a >2.5x margin. Re-verified after
      the fix: a real stack (sheet 1 double-sided, blank separator, 3
      more double-sided sheets) with `--split-on-blank` produced exactly
      two PDFs, 2 pages and 6 pages, matching the physical layout.
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
- [x] **Fixed 2026-09-14, hardware-verified:** OCR quality on German
      documents. `pdf_builder.py`'s `ocrmypdf` call had no `-l`/`--language`
      flag at all, so it silently defaulted to English-only recognition —
      degrading accuracy on umlauts/ß/German word segmentation even after
      installing the `deu` language pack (`brew install tesseract-lang`),
      since nothing ever told `ocrmypdf` to use it. Now passes
      `-l deu+eng` (`OCR_LANGUAGE` in `pdf_builder.py`) by default.
      Re-verified with a real German document: words containing umlauts
      and ß (e.g. "Grüße", "Datenschutzgrundverordnung") extracted correctly.
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
`~/Library/Application Support/scanix500/profiles.json`. A fresh install is
seeded with two profiles on first run: "Default" and "Hardware Button" (the
latter is what the physical Scan button triggers — see
[Phase 3](#phase-3-physical-scan-button-trigger) below). Both are ordinary
profiles and can be edited or deleted like any other.

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

- [x] **Fixed 2026-09-13, hardware-verified:** the app crashed on every
      single launch with `NSInternalInconsistencyException - Item to be
      inserted into menu already is in another menu`. `_rebuild_menu()`
      (called from `__init__`) re-added `self.quit_button` before rumps'
      own `initializeStatusBar()` (which runs later, at the end of
      `App.run()`) had ever added it itself — the same `NSMenuItem`
      object can't belong to two menus, and Cocoa raised on the second
      add even though rumps' own title-keyed dict logic would have
      silently ignored the "duplicate." Fixed by only re-adding
      `quit_button` on rebuilds that happen after the app has actually
      started (tracked via a `self._app_started` flag), not the initial
      one from `__init__`. Verified by actually launching
      `scanix500-menubar` and confirming the icon appears in the real
      menu bar with no crash.
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
- [ ] Delete Profile: confirmation prompt appears; deleting the last
      remaining profile is refused with a clear alert instead of silently
      failing or leaving an empty menu.
- [ ] Clicking a profile, or Add/Edit/Delete, while a scan is already in
      progress does nothing (no double-scan, no crash).
- [ ] After adding/editing/deleting a profile, the menu still has a working
      Quit item (the menu is rebuilt from scratch on each change).
- [ ] Quit and relaunch `scanix500-menubar`: profiles persist correctly
      from `profiles.json`.
- [ ] With paper loaded in the ADF, `curl -i -X POST
      "http://127.0.0.1:8765/scan?skip_blank_filter=false&skip_ocr=false&split_on_blank=false"`
      — the menu bar icon changes to the scanning state, the request
      blocks until the scan completes, and the response is `200` with a
      JSON body naming the output PDF path.
- [ ] The same response's `files` field contains a `content_base64` entry
      for that PDF — decode it locally (e.g.
      `python3 -c "import base64,sys; open('out.pdf','wb').write(base64.b64decode(sys.stdin.read()))"`,
      piping the field's value in) and confirm it opens as a valid PDF.
- [ ] `curl -i http://127.0.0.1:8765/health` → `200` with
      `{"service": "scanix500-bridge"}`, and the menu bar icon does
      **not** change to its scanning state (proves the probe never
      touches the scanner).
- [ ] `curl -i -X POST "http://127.0.0.1:8765/scan?skip_blank_filter=false"`
      (missing `skip_ocr`/`split_on_blank`) → `400`, naming both missing
      parameters.
- [ ] While a scan is running (from the check above, or a physical button
      press), a second `curl -i -X POST
      "http://127.0.0.1:8765/scan?skip_blank_filter=false&skip_ocr=false&split_on_blank=false"`
      from another terminal → `409`.
- [ ] `curl -i -X OPTIONS http://127.0.0.1:8765/scan` → `204` with
      `Access-Control-Allow-Origin: *`.
- [ ] **"Set Bridge Scan Folder…"** appears in the menu; choosing a new
      folder and then running the smoke-test `curl` above writes the
      safety-net PDF into that new folder, not the old one — confirm by
      checking the folder's contents before and after.
- [ ] From Dossiary's own page (once that side exists), clicking Scan
      produces a readable response body, not an opaque CORS failure —
      `curl` above exercises none of the browser's own CORS behavior, and
      CORS is the entire reason the bridge's OPTIONS handling and
      `Access-Control-Allow-Origin` header exist in the first place.

## Phase 3: Physical Scan-button trigger

No install step beyond what Phase 2 already needs — the button watcher is
built into the menu bar app (`scanix500-menubar`) and requires no extra
dependency.

See [`docs/superpowers/specs/2026-09-13-scanix500-button-trigger-design.md`](docs/superpowers/specs/2026-09-13-scanix500-button-trigger-design.md)
for the design and [`docs/superpowers/plans/2026-09-13-scanix500-button-trigger.md`](docs/superpowers/plans/2026-09-13-scanix500-button-trigger.md)
for the implementation plan.

### Use

With the menu bar app running, press the physical Scan button on the iX500
(with paper loaded in the ADF): a scan starts using the "Hardware Button"
profile, exactly as if you had clicked that profile in the menu. That profile
is created automatically on a fresh install, and added automatically on first
launch for an installation that predates this feature — no manual step either
way. It is an ordinary profile: edit its destination and flags to change what
the button does. If you delete it, the button becomes a permanent no-op — the
profile is not re-created on the next launch.

### Manual checklist

`button_watcher.py` and the `rumps.Timer` wiring in `app.py` have no
automated tests, since they're live hardware/GUI code:

- [ ] Launch `scanix500-menubar` with an existing `profiles.json` from a
      Phase 2 install (i.e. one that does NOT yet have a "Hardware Button"
      profile): confirm the profile is added automatically (check
      `~/Library/Application Support/scanix500/profiles.json` after
      launch, or check the "Edit Profile…"/"Delete Profile…" submenus)
      without any manual step.
- [ ] Press the physical Scan button on the iX500 (with paper loaded):
      confirm a scan starts using the "Hardware Button" profile — icon
      changes to the scanning state, then a completion notification
      appears, exactly like clicking that profile in the menu would.
- [ ] Press the button while a scan is already in progress (from a menu
      click or a previous button press): confirm nothing happens (no
      double-scan, no crash) — same guard as clicking a profile mid-scan.
- [ ] Unplug the scanner while the menu bar app is running: confirm no
      crash, no error notification spam — the app just quietly stops
      detecting presses until it's plugged back in.
- [ ] Press the button once: confirm exactly one scan runs, and no second
      scan starts after it completes (proves the button's sensor state
      clears cleanly after being read, matching the confirmed `0 → 1 → 0`
      transition from testing).
- [ ] Delete or rename the "Hardware Button" profile via the Delete/Edit
      Profile menu, then press the physical button: confirm it's a silent
      no-op (no crash, no notification) rather than triggering the wrong
      profile. Then quit and relaunch `scanix500-menubar`: confirm the
      deleted profile has NOT come back (the one-time migration is recorded
      by a `.hardware_button_seeded` marker file next to `profiles.json`).
- [ ] While the menu bar app is running and polling, confirm another
      application (e.g. VueScan, Image Capture) can still open and use the
      scanner in between polls — the device is not held open continuously.

## Phase 4: HTTP bridge

No install step beyond what Phase 2 already needs — the bridge is built
into the menu bar app (`scanix500-menubar`) and requires no extra
dependency (`http.server` is part of the Python standard library).

### Use

With the menu bar app running, `POST /scan?skip_blank_filter=<true|false>&skip_ocr=<true|false>&split_on_blank=<true|false>`
to `http://127.0.0.1:8765` triggers a scan using exactly those settings —
no profile involved — and blocks until the scan finishes, returning a
JSON body:

```json
{"ok": true, "partial": false, "message": "/path/to/scan.pdf", "output_paths": ["/path/to/scan.pdf"], "files": [{"filename": "scan.pdf", "content_base64": "JVBERi0xLjQK..."}]}
```

All three query parameters are required and must be exactly `true` or
`false`; a missing or malformed one is a `400` naming which parameter(s)
were the problem. `ok: false` with `partial: true` means a partial scan
(e.g. a multi-feed jam) still produced a usable file, named in
`output_paths`. `ok: false` with `partial: false` means the scan failed
outright. A `409` means a scan is already in progress (from any trigger —
menu, hardware button, or another bridge request) and this request was
rejected immediately, not queued.

Every bridge-triggered scan writes its safety-net copy to one shared
folder, not a per-request destination — see **"Set Bridge Scan
Folder…"** below.

**`files` carries the same file(s) named in `output_paths`, base64-encoded**,
so a caller (like Dossiary) doesn't need filesystem access to the
destination folder at all — it can decode each `content_base64` entry and
write the bytes wherever it needs them. `output_paths` is still the
on-disk record: every file is written to the configured Bridge Scan
Folder as an unconditional safety net *before* this response is built,
regardless of whether the caller ever reads `files`. A path that can no
longer be read by the time the response is built (removed, permissions)
is simply omitted from `files` rather than failing the whole response.
Because of that, `files` may be shorter than `output_paths` — don't
assume `files[i]` corresponds to `output_paths[i]`; match entries by
`filename` instead.

**A bridge request blocks until the scan actually resolves — there is no
server-side timeout.** It will also block for as long as any modal dialog
is open in the menu bar app (Add/Edit/Delete Profile, or the folder
picker), since AppKit only services the main thread's queued work in
`NSDefaultRunLoopMode`, and an open modal panel runs the main loop in
`NSModalPanelRunLoopMode` instead — a caller (like Dossiary) that needs to
avoid an indefinite wait should apply its own client-side timeout.

The port defaults to `8765`; override it by setting `SCANIX500_BRIDGE_PORT`
before launching `scanix500-menubar`.

**Smoke test** (with paper loaded in the ADF):

    curl -i -X POST "http://127.0.0.1:8765/scan?skip_blank_filter=false&skip_ocr=false&split_on_blank=false"

**`GET /health`** is a separate, lightweight endpoint a caller can probe
before ever attempting a scan — `200` with `{"service":
"scanix500-bridge"}` immediately, no busy-guard interaction, no scanner
involvement. It exists so a client can auto-detect whether the bridge is
running on the default port before falling back to asking the person to
confirm the port manually:

    curl -i http://127.0.0.1:8765/health

**Every bridge-triggered scan's safety-net copy is written to one shared
folder**, configured via the menu bar app's own **"Set Bridge Scan
Folder…"** menu item (a native folder picker — the same one Add/Edit
Profile already uses). Defaults to `~/Documents/Scans` until you set it
explicitly. This is deliberately not a per-request destination — the
caller (Dossiary) already receives the scanned bytes directly in `files`
(see above), so this folder exists purely as a local backup, not as
something a caller picks per scan.

**Trust model**: the bridge binds to `127.0.0.1` only and has no
authentication, `Host` validation, or `Origin` validation, and it returns
`Access-Control-Allow-Origin: *` on every response (required so Dossiary,
a `file://` page sending `Origin: null`, can read the response at all — a
narrower allow-list can't accommodate that). This is broader exposure
than "physical access to this machine": a plain cross-origin `POST`
doesn't need CORS permission to be *sent*, only for its *response* to be
readable, and the wildcard header here makes it readable too — so **any
web page you visit in any browser on this machine, or any other local
process**, can `POST` to `http://127.0.0.1:8765/scan`, trigger a
real physical scan, and read back both `output_paths` (absolute
filesystem paths that disclose your username and library location) and,
via the `files` field, the complete contents of whatever was just
scanned — not just its location, but the actual document. This isn't a
flaw to fix here — binding to loopback with no auth is a deliberate
design choice, and Dossiary's `file://`-origin requirement is exactly
what rules out a narrower CORS allow-list — but don't run this on a
shared or networked machine, and understand that the risk isn't limited
to other people with access to this machine: it includes ordinary web
browsing on it, by anyone using it.

### Dossiary integration

[Dossiary](https://github.com/AarneAarebye/Dossiary) (a separate app) has
its own "Scan"/"Scan Multi" toolbar buttons that call this bridge. Both
sides are in sync as of Dossiary v1.18.0: it health-probes the bridge on
the default port, calls the parameterized `POST /scan` endpoint directly
with the settings each button wants, and reads `files` to write the scan
into whichever library's `inbox/` is currently open. There's no profile
to create and no name to keep in sync — just run `scanix500-menubar` and
Dossiary's Scan/Scan Multi buttons auto-connect on first click, with no
manual URL entry unless the bridge is running on a non-default port (set
via Dossiary's Field Settings, `scan_bridge_url`).

Optionally, change **"Set Bridge Scan Folder…"** if you don't want the
safety-net copy landing in `~/Documents/Scans`.
