# scanix500: a durable, vendor-independent scan pipeline for the ScanSnap iX500

## Background

Ricoh/PFU's ScanSnap Home no longer activates this iX500 unit — the scanner is listed but shows a red-error badge and a greyed-out Scan button. This is a software-lifecycle decision on Ricoh's side (ScanSnap Manager's distribution/support ended November 2024, wireless/Connect-app support for older models has been dropped, and activation requirements have tightened in ScanSnap Home 3.0+), not a hardware fault. The scanner itself is fully functional.

Rather than chase Ricoh's shrinking support window (reinstalling legacy ScanSnap Manager, which is itself EOL and will likely break on a future macOS upgrade) or buy into another vendor's lifecycle (VueScan), this project drives the scanner directly via the open-source SANE `fujitsu` backend, which has had funded, maintained iX500 support for years. Everything above the driver layer is ours to control indefinitely.

## Goal

Replace ScanSnap Home's day-to-day functionality with a small, owned pipeline that:
- captures full ADF duplex scans (both sides, one feed pass — the iX500 has two CIS sensors, not a flip mechanism)
- removes blank backsides automatically
- detects multi-feed/jam conditions instead of silently producing bad pages
- optionally uses a blank sheet as a document separator to split one physical batch into multiple output PDFs
- produces searchable (OCR'd) PDFs
- routes finished PDFs to a configured destination folder

Non-goals: no ScanSnap Cloud integration, no mobile app, no multi-user/network scanning, no attempt to preserve ScanSnap Home's UI or profile system.

## Known technical constraint

`scanimage`/`scanadf` (the standard SANE CLI tools) have a long-standing, unresolved bug where ADF Duplex mode doesn't correctly return both image streams for the iX500 ([sane-project/backends#411](https://gitlab.com/sane-project/backends/-/issues/411)). `simple-scan`, built on the same `fujitsu` backend, gets full duplex working correctly — proving the driver itself is capable, and that the bug is specific to how those CLI tools consume the two-stream output. This pipeline therefore talks to libsane directly via the `python-sane` bindings rather than shelling out to `scanimage`/`scanadf`, doing its own `sane_start()`/`sane_read()` loop per physical page to pull and pair both streams correctly.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Trigger layer                               │
│  Phase 1: CLI command (scanix500)            │
│  Phase 2: menu bar app (rumps)               │
│  Phase 3: physical button (research spike)   │
└───────────────────┬───────────────────────────┘
                    │ invokes
┌───────────────────▼───────────────────────────┐
│  Capture & process pipeline (Python)           │
│  sane_capture.py → blank_page_filter.py        │
│                  → build_pdf.py                │
│                  → route_output.py             │
└───────────────────┬───────────────────────────┘
                    │ writes
┌───────────────────▼───────────────────────────┐
│  Output: one or more searchable PDFs in a      │
│  configured destination folder                 │
└─────────────────────────────────────────────────┘
```

Foundation: Homebrew `sane-backends` (`fujitsu` backend) talking to the iX500 over USB. No Ricoh account, no activation, no network dependency.

## Components

**`sane_capture.py`**
- Opens the `fujitsu` backend device via `python-sane`, sets source to `"ADF Duplex"`.
- Runs a per-sheet `sane_start()`/`sane_read()` loop, pulling both front and back image streams each cycle and pairing them in physical order.
- Reads the backend's multi-feed/jam sensor status option each cycle; raises a structured error identifying the sheet position if triggered.
- Output: an ordered list of page images (front/back pairs).

**`blank_page_filter.py`**
- Computes an ink-coverage/variance metric per page against a configurable threshold to identify blanks.
- Always drops detected blanks from the output.
- `--split-on-blank` flag: additionally treats each detected blank's position as a document boundary, partitioning the page stream into N sub-documents. Default off — without the flag, a blank sheet is just removed and the run produces a single PDF, same as today.

**`build_pdf.py`**
- Assembles surviving pages into a PDF per partition.
- Runs `ocrmypdf` (Tesseract) to produce a searchable text layer, unless `--skip-ocr` is passed, in which case it assembles an image-only PDF.
- OCR failure on an individual page falls back to including that page as image-only and logs a warning; it does not fail the whole batch.

**`route_output.py`**
- Writes each finished PDF to a configured destination folder with a timestamp-based filename (sequentially numbered when `--split-on-blank` produced multiple documents).
- Kept separate from `build_pdf.py` so destination routing rules can evolve independently later.

**`scanix500` (CLI entry point)**
- Wires the above into one command: capture → filter (+ optional split) → OCR (or skip) → route.
- Flags: `--skip-blank-filter`, `--skip-ocr`, `--split-on-blank`, destination folder argument.
- The only entry point Phase 2 (menu bar) and Phase 3 (button) call into.

## Data flow

```
scanix500 [flags] <destination>
  → sane_capture.py: full ADF Duplex loop, stops when ADF reports empty
  → blank_page_filter.py: drop blanks; if --split-on-blank, partition stream
  → for each partition:
       build_pdf.py: OCR (unless --skip-ocr) and assemble
       route_output.py: write to destination, sequential filename if >1
  → print result path(s) to stdout
```

One invocation with no `--split-on-blank` produces exactly one PDF. This is intentionally simple — no queuing, no session/batch management beyond blank-sheet partitioning.

## Error handling

- **Scanner not found**: fail fast with a clear message before any capture begins; no partial output.
- **Multi-feed/jam mid-scan**: stop the capture loop, keep pages already successfully captured, report the sheet position that triggered it, exit non-zero.
- **OCR failure on one page**: don't fail the batch; include that page image-only and log a warning naming it.
- **Empty ADF / no pages fed**: exit cleanly reporting "no pages scanned," not an empty PDF.

## Testing

- **Pure logic, automated**: blank-page threshold detection (synthetic images), blank-position partitioning, filename generation, error-message formatting.
- **Capture/duplex/multi-feed/OCR, manual checklist against real hardware**: no mocking the scanner — hardware quirks are the point of this project. Checklist includes a multi-page duplex stack, a deliberate double-feed, and a run with a blank separator sheet mid-stack.

## Phasing

1. **`scanix500` CLI** (core deliverable) — full functional replacement for ScanSnap Home's day-to-day use.
2. **Menu bar app** (`rumps`) — thin wrapper around the CLI, macOS notification on completion.
3. **Physical Scan button** (research spike, not guaranteed) — check whether the `fujitsu` backend exposes a pollable button-status option for the iX500; if so, a small `insaned`-style poller triggers `scanix500` on press. If the backend doesn't expose it, this phase is dropped and the menu bar remains the primary trigger.
