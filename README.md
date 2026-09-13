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

Design and implementation plan complete. Code not yet implemented — see
the plan above for the task-by-task build.

## Install (once implemented)

    brew install sane-backends ocrmypdf tesseract
    pip install python-sane
    pip install -e ".[dev]"

## Run (once implemented)

    scanix500 ~/Documents/Scans
    scanix500 ~/Documents/Scans --split-on-blank
    scanix500 ~/Documents/Scans --skip-ocr
    scanix500 ~/Documents/Scans --skip-blank-filter

## Automated tests (once implemented)

    pytest

These cover all pure logic (capture pairing/multi-feed/empty-ADF handling,
blank detection and splitting, PDF assembly and OCR fallback, output path
naming, and CLI wiring) using fakes and mocks — no scanner hardware
required. Hardware-touching behavior (real SANE device I/O, the actual
multi-feed sensor, real OCR quality) is verified against the physical
scanner via the manual checklist in the plan document, not automated.
