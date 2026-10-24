# Canon CanoScan 8800F: flatbed documents

Date: 2026-10-24
Repo: iX500 (no protocol change; dossiary-scan-helper's `PROTOCOL.md` stays as it is)

## Goal

A Canon CanoScan 8800F, plugged into the same computer as the iX500 (the
Pi running `scanix500-server`, or a Mac running `scanix500-menubar`), shows
up as a second scanner in Dossiary and scans single flatbed pages to
(searchable) PDF.

This is sub-project 1 of three. Not in this spec, each its own later
spec/plan cycle:

2. Photos: JPEG/TIFF output and resolutions above 600 dpi. Needs protocol
   additions (`format`, more resolutions) and helper/Dossiary work.
3. Film: the transparency unit. Needs a new `source` in the protocol. SANE's
   changelog says negative scanning in transparency mode was added for the
   8800F in 1.0.23 and disabled again in 1.0.24, so expect slides only;
   untested.

## What was verified (node-000, Debian 13, 2026-10-24)

- `scanimage -L` lists `pixma:04A91901`, "CANON Canoscan 8800F", next to
  `fujitsu:ScanSnap iX500:1203900`. `libsane-pixma` is installed and enabled;
  its library contains the 8800F model entry. USB id `04a9:1901`.
- Options: `--source Flatbed|Transparency Unit`, `--mode
  Color|Gray|Lineart|48 bits color|16 bits gray`, resolutions 75..4800,
  scan area 216 x 297 mm (A4 and a little more), `--calibrate Once|Always|Never`.
- The Mac has `sane-backends` 1.4.0 with `pixma` enabled.
- `ioreg -p IOUSB` lists vendor/product ids without opening SANE.
- `PROTOCOL.md` already allows `sources: ["flatbed"]`, several scanners in
  `GET /scanners`, and a generic request validation; a flatbed scan is one page.

Not yet verified (manual checklist below): an actual 8800F scan through
`python-sane` (the Pi and the Mac), a transparency scan. The buttons are verified (section 5).

## Design

### 1. Scanner catalogue and discovery (`scanners.py`, new)

A table of known scanners. Each entry: protocol id, display name, USB
vendor/product id, a SANE device matcher, the `GET /scanners` entry
(today's `IX500` dict moves here), the not-connected message.

| id | name | sources | duplex | colorModes | resolutions | extras |
|---|---|---|---|---|---|---|
| `sane:ix500` | ScanSnap iX500 | feeder | yes | color, gray, bw | 300, 600 | splitOnBlank, skipBlankPages |
| `sane:canoscan-8800f` | Canon CanoScan 8800F | flatbed | no | color, gray, bw | 150, 300, 600 | none |

OCR languages as for the iX500 (intersected with what's installed).

`attached_scanners() -> list[dict]` returns the catalogue entries whose USB
id is present. Linux: read `/sys/bus/usb/devices/*/{idVendor,idProduct}`.
macOS: parse `ioreg -p IOUSB -l` (`idVendor`/`idProduct`). **Neither opens
SANE**, so nothing races a running scan or the button poller. If the USB
list can't be read, it returns an empty list and never raises into the HTTP
thread.

`RealBackend.scanners()` returns `attached_scanners()` instead of the fixed
iX500 entry. Consequence, on purpose: an unplugged iX500 no longer appears in
the list (it did before). `POST /scan` for a scanner that isn't attached
still answers 503 with that scanner's message; a scan request for an id not in
the catalogue is still 404. The fake backends are unchanged.

### 2. Capture (`capture.py`)

- `PySaneFlatbed`, next to `PySaneDevice`, implements the existing
  `SaneDevice` protocol. It finds its SANE name via the catalogue's matcher
  (`pixma` / "8800F"), sets `source=Flatbed`, the mode from `SANE_MODES` and the
  resolution, scans once, returns that one frame, then raises `StopIteration`.
  `multi_feed_detected()` is always `False`.
- `capture_pages(device, duplex=False)` is reused unchanged.
- The scan area is left at the driver default (the whole glass).
- The driver's calibration (`Once`) makes the first scan after a service
  restart slower; left at the default.
- Errors map to the existing `ScannerNotFoundError` / `ScannerSetupError`.

### 3. Carrying the scanner choice through (`profiles.py`, `bridge.py`, `runner.py`, `cli.py`)

Scans run as a subprocess of the `scanix500` CLI built from a `Profile`, and
`profile_for(settings, destination)` today names no scanner.

- `Profile` gains `scanner` (default `"sane:ix500"`; old `profiles.json` files
  load unchanged).
- `profile_for()` copies the request's scanner id into it.
- `runner.py` passes it as a new CLI option `--scanner <id>`; `cli.py` picks
  the capture device class from the catalogue.
- Menu profiles keep the iX500 (their `scanner` is the default). A button
  scan sets `scanner` from the button that was pressed (the iX500's button
  gives `sane:ix500`, the 8800F's gives `sane:canoscan-8800f`), whatever the
  stored "Hardware Button" profile says (see section 5).

### 4. Pipeline for flatbed pages (`cli.py`)

One A4 page at the requested mode and dpi, **pass-through**:
no `trim_background`, no `snap`, no blank-page filter (the lid is white, so
trim finds no background, snap changes nothing on an A4 scan, and a single page
needs no blank check). Black & white: scanned as Gray, thresholded with
`bw.py` (160), as on the iX500. OCR (inline or as a background job) and
ocrmypdf's `--deskew` go through the existing `pdf_builder`/`OcrJobQueue`
path. The iX500 path is not touched.

`extras` `splitOnBlank` / `skipBlankPages` on the 8800F are refused by the
existing generic validation (not in its `extras`). `duplex: true` likewise.

### 5. The 8800F's buttons, Mac only (`menubar/`)

Verified on the real 8800F (2026-10-24, `scripts/probe-pixma-buttons.py`):
SANE's `target` option reads the pressed button's number for about one poll
and 0 otherwise, when polled the way the menu bar app polls (open, read, close):

| `target` | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| button | COPY | PHOTO/FILM | E-MAIL | COLOR | BLACK & WHITE | CUSTOM | FINISH |

`button-update` can't be set through python-sane, and `button-1` blips to 1
about every 12 seconds while idle, so only `target` is read.

On the Mac the three PDF scan buttons start a flatbed scan with the reserved
"Hardware Button" profile, its destination and the usual notification: COLOR
scans in color, BLACK & WHITE in black & white (overriding the profile's mode),
CUSTOM uses the profile as it is. COPY, PHOTO/FILM, E-MAIL and FINISH do nothing
(FINISH ends a multi-page PDF, which a single flatbed page never needs). The
profile's feeder-only settings (duplex, split on blank) are ignored for the
glass. `scanix500-server` doesn't act on the 8800F's buttons (the iX500's
button on the server only pairs computers; unchanged). `button_watcher.py`
gets, per scanner, a reader returning the profile overrides a press asks for
(`{}` = none, `None` = no press): the iX500's reader wraps the existing
`button_pressed`, the 8800F's is `pixma_button_overrides`. Each poller opens only its own
device, and the existing busy guard keeps one scan at a time across both
scanners and all triggers. A tap shorter than the poll interval could be missed;
all seven buttons were caught in the probe.

### 6. Concurrency

One scan at a time on a machine, whichever scanner or trigger: the same
`ServerScanTrigger` / `self._scanning` busy guard; a second request gets the
existing 409.

## Error handling

- 8800F not attached: not in `GET /scanners`; `POST /scan` 503 with
  "The Canon CanoScan 8800F isn't connected. Check that it's plugged in and
  switched on." (translated through `t()` like the iX500 message).
- SANE can't open it / refuses a setting: the existing error classes and
  messages, with the scanner named.
- USB discovery failure: nothing attached, never an exception in the HTTP thread.

## Testing

Automated:
- `scanners.py`: USB discovery parsers against captured sysfs and `ioreg`
  samples (both scanners, one, none, garbage); catalogue entries validate
  against `PROTOCOL.md`'s shape.
- `PySaneFlatbed` via the `SaneDevice` / `FakeSaneDevice` pattern: one frame
  then `StopIteration`; `capture_pages(duplex=False)` gives one page.
- `Profile` round trip with and without `scanner`; `profile_for`; CLI
  `--scanner`; runner argument list.
- `cli.py` flatbed path: no trim/snap/blank filter, bw thresholded, OCR path
  taken (mocked at the capture boundary).
- `RealBackend.scanners()` with a stubbed `attached_scanners`; `POST /scan`
  503/404/409 cases. The fake-scanner conformance run (51 tests) stays green.
- `tests/server/test_runtime.py`'s no-rumps/AppKit check still passes.

Manual, on real hardware (not mocked, per this repo's rule):
- Pi (node-000): `GET /scanners` lists both when both are attached and only one
  when the other is unplugged; an 8800F scan at 150/300/600, gray/color/bw,
  with and without OCR; first-scan calibration time; both scanners attached
  and a scan on each in turn.
- Mac: the same through the menu bar app's bridge; the COLOR, BLACK & WHITE and
  CUSTOM buttons start a scan with the right mode and the other four do nothing;
  polling doesn't disturb a scan; the iX500 button still works with both attached.
- A transparency scan is *not* part of this sub-project (only noted for 3).

## Release

Version 0.8.0 (`pyproject.toml` and `__version__` together, tag `v0.8.0`,
`scripts/install-app.sh`, the `.deb` builds from the tag). README, AGENTS.md
(constraints list and the "iX500 is always listed" sentence in the protocol v2
bullet) and `docs/` updated. No change to `PROTOCOL.md` or dossiary-scan-helper.

## Known hazards

- node-000 has hung hard (power-cycle needed) each time a USB scanner was
  hot-plugged; cause unproven (suspected power dip on the Pi 4's shared USB
  budget). Plug scanners in with the Pi off until that is settled.
  journald on node-000 now keeps logs across boots (`SyncIntervalSec=1s`).
