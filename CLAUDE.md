# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

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
