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

These cover all pure logic (capture pairing/multi-feed/empty-ADF handling,
blank detection and splitting, PDF assembly and OCR fallback, output path
naming, and CLI wiring) using fakes and mocks — no scanner hardware
required.

## Manual hardware checklist

Run these by hand against the real iX500 after any change to `capture.py` or
`pdf_builder.py` — hardware behavior isn't mocked:

- [ ] `scanimage -L` and/or `sane.get_devices()` shows the iX500 via the
      `fujitsu` backend. If not, `PySaneDevice`'s device-matching string
      needs adjusting (see Task 3's note).
- [ ] Load a 5-page double-sided stack, run `scanix500 <dest>`: resulting
      PDF has 10 pages in correct front/back/front/back order.
- [ ] Load a stack with one intentionally blank backside: resulting PDF
      has that blank page removed.
- [ ] Load a stack with a fully blank separator sheet in the middle, run
      with `--split-on-blank`: two separate PDFs are produced.
- [ ] Deliberately feed two sheets stuck together: `scanix500` reports a
      multi-feed error and exits non-zero, and the pages captured before
      the jam are still written to a PDF. Confirm `PySaneDevice`'s
      `multi_feed_detected()` option name actually reflects the sensor
      (adjust per Task 3's note if the option name is wrong for this
      firmware/backend version).
- [ ] Resulting PDF text is selectable/searchable (OCR ran) unless
      `--skip-ocr` was passed.
