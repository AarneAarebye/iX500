# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## What this project is

`scanix500`: a small, owned Python pipeline that drives a Fujitsu/Ricoh
ScanSnap iX500 scanner directly via SANE, replacing ScanSnap Home (which
no longer activates this scanner — a vendor lifecycle decision, not a
hardware fault). Full context lives in:

- `docs/superpowers/specs/2026-09-13-scanix500-pipeline-design.md` — design, architecture, rationale
- `docs/superpowers/plans/2026-09-13-scanix500-cli-pipeline.md` — task-by-task implementation plan for Phase 1 (the `scanix500` CLI)

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
  only the small `ScanixMenuBarApp.trigger()` integration in `app.py` is
  thread-marshaling/AppKit-dependent code, following this repo's existing
  "hardware/live-GUI code is manually verified, not mocked" precedent.
  Fixed, two-way contract with Dossiary: profiles named exactly
  `"Dossiary Scan"`/`"Dossiary Scan Multi"` must exist for its toolbar
  buttons to work — see README.md's own "Dossiary integration" section.
- **Extended 2026-09-16** (see
  `docs/superpowers/specs/2026-09-16-scan-bridge-auto-connect-design.md`,
  plan `docs/superpowers/plans/2026-09-16-scan-bridge-auto-connect.md`):
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

## Project phasing

This repo currently covers **Phase 1 only** (the CLI pipeline). Phase 2
(menu bar app) and Phase 3 (physical Scan-button trigger, a research
spike — feasibility on macOS is unconfirmed) are follow-on work with
their own future specs/plans; don't assume they exist yet.

## Conventions

- Python 3.11+, `pytest` for tests, `pyproject.toml` (hatchling) for packaging.
- Follow the plan's exact file structure and function signatures — later
  tasks depend on the names/types defined in earlier ones.
- Work through the plan using the `superpowers:subagent-driven-development`
  or `superpowers:executing-plans` skill, task by task, TDD (failing test
  → minimal implementation → passing test → commit) per step.
