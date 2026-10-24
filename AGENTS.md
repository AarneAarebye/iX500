# AGENTS.md

Guidance for AI coding agents (Claude Code and others) working in this repository.

## What this project is

`scanix500`: a small, owned Python pipeline that drives a Fujitsu/Ricoh
ScanSnap iX500 scanner directly via SANE, replacing ScanSnap Home (which
no longer activates this scanner — a vendor lifecycle decision, not a
hardware fault). Full context lives in:

- `docs/superpowers/specs/2026-09-13-scanix500-pipeline-design.md` — design, architecture, rationale for Phase 1 (the `scanix500` CLI)
- `docs/superpowers/plans/2026-09-13-scanix500-cli-pipeline.md` — task-by-task implementation plan for Phase 1
- `docs/superpowers/specs/2026-09-13-scanix500-menubar-design.md` + `docs/superpowers/plans/2026-09-13-scanix500-menubar-app.md` — Phase 2 (`scanix500-menubar`)
- `docs/superpowers/specs/2026-09-13-scanix500-button-trigger-design.md` + `docs/superpowers/plans/2026-09-13-scanix500-button-trigger.md` — Phase 3 (physical Scan-button trigger)
- `docs/superpowers/plans/2026-09-14-scanix500-http-bridge.md` (Phase 4's original plan), plus its later amendments `docs/superpowers/plans/2026-09-16-scan-bridge-auto-connect.md` and `docs/superpowers/plans/2026-09-16-scan-bridge-parameterized-scan.md` — the HTTP bridge; each amendment's own design spec lives in the sibling Dossiary repo (see "Project phasing" below)
- `docs/superpowers/plans/2026-10-11-scanix500-protocol-v2.md` — protocol version 2 (0.4.0): `GET /scanners`, JSON `POST /scan`, scan settings; contract is dossiary-scan-helper's `PROTOCOL.md`
- `docs/superpowers/specs/2026-09-14-scanix500-profile-form-design.md` + `docs/superpowers/plans/2026-09-14-scanix500-profile-form.md` — the Add/Edit Profile settings window

Read the spec before making architectural changes; read the plan before
implementing — it defines exact file layout, function signatures, and
TDD steps.

## Key constraints (see spec for full rationale)

- No Ricoh/ScanSnap account, cloud service, or vendor software dependency anywhere.
- ADF Duplex capture must go through `python-sane` directly, not
  `scanimage`/`scanadf` — those CLI tools mishandle the iX500's dual
  front/back image-stream output. This is a confirmed upstream bug, not
  a choice to revisit casually.
- Multi-feed/jam detection must preserve pages already captured, never
  discard partial work.
- OCR failure on one page must not fail the whole batch.
- Hardware-touching code (`PySaneDevice`, the real multi-feed sensor
  option name, actual OCR quality) is verified manually against the
  physical scanner, per the checklist in the plan/README — do not try to
  mock SANE hardware behavior in automated tests; mock only at the
  orchestration-logic boundary (see `capture.py`'s `SaneDevice` Protocol
  and `FakeSaneDevice` test double for the pattern to follow).
- **The HTTP bridge (`src/scanix500/menubar/bridge.py`) is embedded in
  `scanix500-menubar`, not a standalone process** — it shares the app's
  existing `self._scanning` busy-guard and `_on_scan_complete()`
  notification path via `ScanixMenuBarApp.trigger()`
  (`ScanTrigger` protocol), so a scan triggered over HTTP is
  indistinguishable, from the app's own perspective, from a menu click or
  a physical button press. `bridge.py` itself has zero rumps/AppKit
  dependency and is fully unit-testable (`tests/menubar/test_bridge.py`);
  only the thread-marshaling/AppKit-dependent bits of `app.py` (actual
  menu rendering, `ScanixMenuBarApp.trigger()`'s cross-thread signaling)
  follow this repo's "hardware/live-GUI code is manually verified, not
  mocked" precedent — `app.py`'s own orchestration logic (`_execute_scan`,
  bridge-startup resilience, `_set_bridge_destination`) is unit-tested via
  `ScanixMenuBarApp.__new__()` plus mocked collaborators, same as any
  other pure logic (see `tests/menubar/test_app.py`).
- **Extended 2026-09-16** (see the design spec in the sibling Dossiary
  repo, `docs/superpowers/specs/2026-09-16-scan-bridge-auto-connect-design.md`
  under `/Users/aarneaarebye/Projects/Paperless/Dossiary`; this repo's own
  plan is `docs/superpowers/plans/2026-09-16-scan-bridge-auto-connect.md`):
  the bridge gained a `GET /health` endpoint — a lightweight reachability
  probe, separate from the scan-triggering `POST /scan/<profile>`, so a
  caller can check the bridge is there without ever risking a real scan
  against an unknown port — and the `POST /scan/<profile>` response
  gained a `files` field: each file named in `output_paths` is also read
  back and base64-encoded into the response, so a caller like Dossiary
  receives the scanned bytes directly over the connection instead of
  relying on `destination` matching a location it can read. `destination`
  remains required (the scan pipeline still needs somewhere to write) but
  is now purely a local safety-net copy — it no longer needs to point at
  any particular Dossiary library's `inbox/`.
- **Extended 2026-09-16 (parameterized scan)** — see
  `docs/superpowers/specs/2026-09-16-scan-bridge-parameterized-scan-design.md`
  in the sibling Dossiary repo, plan
  `docs/superpowers/plans/2026-09-16-scan-bridge-parameterized-scan.md`:
  `POST /scan/<profile-name>`'s lookup is gone. Dossiary now sends its
  scan settings directly as query parameters
  (`skip_blank_filter`/`skip_ocr`/`split_on_blank`, each required and
  exactly `true`/`false`), and `route_scan_request()` builds an ephemeral
  `Profile` from them rather than looking one up — no more requirement
  that `"Dossiary Scan"`/`"Dossiary Scan Multi"` profiles exist at all.
  `profiles.json`, the manual dropdown, and the reserved `"Hardware
  Button"` profile are untouched. The safety-net destination for a
  bridge-triggered scan now comes from a small, separate,
  menu-editable setting (`bridge_settings.py`), not a profile's own
  `destination` field — see README.md's own "Set Bridge Scan Folder…"
  note.
- **Pairing (0.3.0)** — `pairing.py` (`PairingStore`), wired into
  `bridge.py` and the menu ("Pair a Browser…", "Forget Paired Browsers…").
  Requests with an `Origin` other than `null`/`http://localhost:<port>`/
  `http://127.0.0.1:<port>` get 403, allowed origins are echoed instead of
  `*`, and `POST /scan` needs `Authorization: Bearer <token>` from
  `POST /pair` (401 otherwise). `GET /health` stays open and adds
  `paired`. This closes the hole where any website (via `Origin: null`
  from a sandboxed iframe) could start a scan and read the document;
  it's the pairing scheme from dossiary-scan-helper's `PROTOCOL.md`,
  applied to the version 1 request. Not backward compatible: Dossiary
  before v1.43.0 can't pair. Only token hashes are stored.
- **Pages come out at their paper's size (0.3.0)** -- the scan area stays
  A4, and three steps shrink it to the sheet: `PySaneDevice` turns on the
  driver's `ald` (the scanner's sensor ends the page at the paper's end)
  and, until 0.4.0, `swcrop`/`swdeskew` (both off since -- see below), and `cli.py` then runs `trim.py`'s
  `trim_background()` on every page before the blank filter, then
  `snap.py`. The trim cuts off a strip at least 8 mm wide at a page's side
  whose columns are both flat (std. dev. < 1.5 on a 1/4-size copy) and gray
  (mean <= 245), never leaving less than 30 mm -- see the protocol version 2
  bullet below for the measurements behind those numbers. Verified with a
  real A5 sheet (148x210 mm out after snapping) and a smooth-paper A4 letter
  (untouched). If a page ever loses content at a side, check those
  thresholds against the real scan before changing anything else.
- **CanoScan 8800F (0.8.0)** -- spec `docs/superpowers/specs/2026-10-24-canoscan-8800f-flatbed-design.md`, plan `docs/superpowers/plans/2026-10-24-canoscan-8800f-flatbed.md`. A second scanner: flatbed, one A4 page per scan, SANE's `pixma` backend (`PySaneFlatbed`), listed as `sane:canoscan-8800f`. The scanner choice travels `POST /scan` -> `Profile.scanner` -> CLI `--scanner`. Its pages skip trim, snap and the blank filter (white lid, one page). Its buttons work on the Mac only (`pixma_button_overrides`: SANE's `target` option is the pressed button's number, table in the spec; COLOR/BLACK & WHITE/CUSTOM start a scan); on the server they do nothing (its `ButtonWatcher` polls only the iX500, whose button pairs computers). Photos are 0.9.0 (below); film is a later sub-project.
- **Photos (0.9.0)** -- spec `docs/superpowers/specs/2026-10-24-photos-protocol-and-8800f-design.md`, plan `docs/superpowers/plans/2026-10-24-photos-ix500.md`; the protocol text and conformance tests are in dossiary-scan-helper (plan `docs/superpowers/plans/2026-10-24-photos-protocol.md` there). The 8800F's entry has `formats` (pdf/jpeg/png), `areas` (a4, a5, a6, 10x15, 13x18) and 1200 dpi; `format`/`area` travel `POST /scan` -> `Profile` -> CLI `--format`/`--area`; `PySaneFlatbed(area=...)` sets br-x/br-y; `images.py` writes the JPEG (quality 92) or PNG (1-bit for bw) with the dpi embedded. `protocol_v2.validate` refuses (422) a format or area the scanner doesn't offer, `ocr` with an image, jpeg with bw, and any scan over `scanners.MAX_PIXELS` (100 megapixels, inclusive; sized for the Pi's 3.4 GB: peak is about three times the raw size). `fake:photo` serves the conformance suite. The macOS helper (0.5.0, local flatbeds and the Pi's scanners) and Dossiary (1.54.0, the scan dialog's Format and Paper size) pass the new fields.
- **`start-menubar.sh` / `stop-menubar.sh`** start or stop the menu bar
  app. Stop unloads the LaunchAgent instead of killing the process, since
  its `KeepAlive` would restart a killed app.
- **Protocol version 2 (0.5.0)** (`menubar/protocol_v2.py`, `bridge.py`,
  `bw.py`, `fakes.py`, `bridge_main.py`). `protocol_v2.py` is pure: the
  scanner catalogue's `/scanners` entries (`scanners.py`: the iX500,
  `sane:ix500`, feeder, duplex, color/gray/bw, 300/600 dpi,
  splitOnBlank/skipBlankPages; and the CanoScan 8800F, `sane:canoscan-8800f`,
  flatbed), request validation, and
  the mapping of a scan result to a response. **`GET /scanners` lists what is
  plugged in, found on the USB bus (`scanners.py`: sysfs on Linux, `ioreg` on
  macOS) and never by opening SANE**: opening SANE from the HTTP thread would
  fight a scan or the Scan button. A known scanner that isn't plugged in is
  validated against its catalogue entry and then refused with `503` on
  `POST /scan`; an unknown id is `404`. Error bodies carry the message under both `message` and
  `error` (Dossiary 1.43 reads `error`). `bridge.py`'s check order: Origin 403
  -> OPTIONS 204 -> unknown path 404 / wrong method 405 with `Allow` ->
  `/scanners` and `/scan` without a valid token 401 -> endpoint checks. **A
  version 1 request is recognised by a query string on `POST /scan`** and goes
  through the old `route_scan_request` unchanged (`{"error"}` bodies);
  `start_bridge_server(..., trigger=None)` disables version 1. OCR stays on for the Scan button and menu profiles. **Background OCR (0.5.0)**: `ocr: true` (with `ocrLanguages`, default `deu+eng`, order = priority) runs the scan with `skip_ocr` and gives every file an `ocrJob`; `GET /ocr/<job>` answers 202/200/404/500 (job ids must match `\A[A-Za-z0-9_-]{22,}\Z`). `OcrJobQueue` (`ocr_jobs.py`) keeps jobs on disk under `~/Library/Application Support/scanix500/ocr-jobs/`, runs one at a time, resumes after a restart, expires after 7 days, and replaces the bridge folder's safety-net copy with the searchable PDF. `RealBackend` without an `ocr_jobs` queue creates and starts one lazily on first use; the app starts one at launch. `fake:ocr` (with in-memory `FakeOcrJobs`, 2 s per job) serves the conformance suite.
  **Black & white is scanned as SANE `Gray` and thresholded to 1-bit (160)
  after trimming and the blank check** (`bw.py`), since both need gray levels;
  checked on the real iX500 (2026-10-11): "ADF Front" for one side, Gray,
  the 160 threshold at 600 dpi, blank detection on gray, OCR on 1-bit PDFs.
  **The driver's `swdeskew` is off since 0.4.0**: on a straight-fed, once-folded
  sheet it rotated the page by about 20 degrees (corners filled with one flat
  gray, 214); with it off the same sheet came out straight and still cropped.
  ocrmypdf's `--deskew` (`pdf_builder.py`) straightens slight tilts instead --
  3 degrees came out straight, 20 degrees was left alone. **The driver's
  `swcrop` is off too** (it cut 13-35 mm of text off a real letter's sides),
  and `trim.py` now counts a column as background only when it is both
  flatter than 1.5 (standard deviation on a 1/4-size copy) and no brighter
  than 245: measured with the driver's processing off, the background is
  0.3-0.5 at 233-241, smooth office paper 2.0-2.7 at 248 (it used to be
  trimmed away), textured paper 6-10. **`snap.py`** then gives a page within
  5 mm of A4, A5, A6, Letter or Legal (either way round; with the A4 scan
  area only A4/A5/A6 can match) exactly that size,
  padding with white or cropping, centred; a real A5 sheet came out 152 mm
  wide after the trim and 148 x 210 mm after snapping. A Paper size choice
  in Dossiary's dialog would need a protocol field and is a separate
  project. **`trim.py` never
  leaves less than 30 mm of a page** (`_MIN_PAPER_MM`): a blank side reads as
  even almost everywhere and was once trimmed to 5 pixels, which then no
  longer looked blank and ended up in the PDF.
  `keep_blank_pages` serves split-only (split on blank pages without skipping
  them). `fakes.py` (`FakeBackend`, `FakePairing`: nine scanners, code
  `000000`) and `bridge_main.py` (`scanix500-bridge --fake-scanners`) exist
  only so `scripts/run-conformance.sh` can run dossiary-scan-helper's
  conformance suite (58 tests) against the bridge.

- **`scanix500.server` is the headless network server** (spec
  `docs/superpowers/specs/2026-10-17-pi-scan-server-design.md`). It reuses
  `bridge.py`'s handler in *remote mode* (`RemoteOptions`: any `Origin`
  header refused, no CORS, `/health` with `fingerprint`/`name`,
  `/pair/button`), `SharedWindowPairingStore` (a code window in a file so
  `scanix500-server pair` can open it for the running service; tokens named
  and revocable), `ServerScanTrigger` (one scan at a time) and
  `ButtonWatcher` (polls the button only while a button-pairing request
  waits). Nothing under `scanix500.server` may import rumps/AppKit --
  `tests/server/test_runtime.py` checks it.

## Project phasing

All four phases are complete and in this repo: Phase 1 (the `scanix500`
CLI pipeline), Phase 2 (`scanix500-menubar`, the menu bar app), Phase 3
(the physical Scan-button trigger — feasible on macOS, confirmed against
real hardware), and Phase 4 (the embedded HTTP bridge for Dossiary,
amended twice since its original plan — see the spec/plan list above).
The Add/Edit Profile settings window is a Phase 2 follow-on, also
complete. See README.md's own "Status" section for the same summary
aimed at a human reader.

## Conventions

- Python 3.11+, `pytest` for tests, `pyproject.toml` (hatchling) for packaging.
- Follow the plan's exact file structure and function signatures — later
  tasks depend on the names/types defined in earlier ones.
- Work through the plan using the `superpowers:subagent-driven-development`
  or `superpowers:executing-plans` skill, task by task, TDD (failing test
  → minimal implementation → passing test → commit) per step.
- **Releasing**: bump `version` in `pyproject.toml` and `__version__` in
  `src/scanix500/__init__.py` together (`tests/menubar/test_bridge.py`
  asserts the version `/health` reports), tag `v<version>`, then install it
  on this Mac with `scripts/install-app.sh v<version>`. The menu bar app
  runs from that installed copy in `/Applications/Dossiary/scanix500/`, not
  from this checkout, so switching branches here doesn't change the scanner
  software in use (spec: Dossiary's
  `docs/superpowers/specs/2026-10-15-applications-folder-layout-design.md`).
  Pushing the tag also builds `scanix500-server_<version>_all.deb`
  (`.github/workflows/deb.yml`: lintian, an install test in
  `debian:bookworm` and `debian:trixie`, then attached to the release); the package files live
  in `packaging/debian/`.
