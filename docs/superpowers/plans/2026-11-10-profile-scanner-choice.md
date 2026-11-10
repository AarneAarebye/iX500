# Profile form: choose the scanner (scanix500 0.10.6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** The menu bar app's profile form gets a "Scanner" dropdown (ScanSnap iX500, Canon CanoScan 8800F) so a profile can use the 8800F. User request 2026-11-10: "How can I make an 8800F profile? I cannot choose a scanner" -> "proper form change".

**Architecture:** Pure, testable logic goes into `menubar/profiles.py` (`profile_for_scanner`, scanner choices); the AppKit form (`menubar/profile_form.py`, interactive only, no automated GUI tests by project precedent) adds an `NSPopUpButton` and uses that logic. Editing a profile keeps every field the form does not show (today `build_profile` rebuilds the Profile from only five fields and silently resets `duplex`, `color_mode`, `resolution`, `format`, `area`, `keep_blank_pages` and `scanner` to defaults — this must stop).

**Tech Stack:** Python 3.11, PyObjC/AppKit, rumps, pytest (`.venv/bin/python -m pytest`).

## Global Constraints

- Scanner ids come from `scanix500.scanners` (`IX500_ID`, `CANOSCAN_8800F_ID`, `KNOWN_SCANNERS` with `.id` and `.name`). Names in the dropdown are the `KnownScanner.name` values (not translated, brand names).
- The 8800F is a flatbed: a profile with `scanner == CANOSCAN_8800F_ID` has `duplex = False` and `split_on_blank = False` (single pages, no separator sheets). Its colour modes/resolutions/formats/areas must remain valid for the entry in `scanners.py` (check `CANOSCAN_8800F`; if a stored value is not offered by the chosen scanner — e.g. resolution 1200 vs list — keep it only if offered, else fall back to 300 / "color" / "pdf" / "a4").
- Switching the dropdown to the 8800F disables (greys out) and unchecks the "Split on blank separator sheets" checkbox; switching back to the iX500 re-enables it (restoring the checkbox state the user had).
- New user-visible text: the label "Scanner:" (the form's existing labels are `t("Name:")`, `t("Destination:")`) in `strings.json` with all 7 languages (`tests/test_i18n.py` guards it; bump its size guard).
- The window gets taller to make room for the new row; keep the existing layout style (frames in points).
- Existing profiles on disk are unchanged; a profile without a `scanner` key keeps loading as the iX500.
- Version 0.10.6 (`pyproject.toml`, `src/scanix500/__init__.py`, version strings in `tests/menubar/test_bridge.py`, README install example); AGENTS.md and README mention the scanner choice.
- Commit messages end with: Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com> and Claude-Session: https://claude.ai/code/session_0117epTz6pLGF3vCWxi4JMsL

---

### Task 1: Scanner choice in the profile form

**Files:**
- Modify: `src/scanix500/menubar/profiles.py`, `src/scanix500/menubar/profile_form.py`, `strings.json`, `tests/test_i18n.py`, version files, `README.md`, `AGENTS.md`
- Test: `tests/menubar/test_profiles.py` (add), plus a test for the form's pure parts if you extract any

**Interfaces:**
- Produces in `profiles.py`: `scanner_choices() -> list[tuple[str, str]]` (id, name; iX500 first), `profile_for_scanner(profile: Profile, scanner_id: str) -> Profile` (returns a copy with `scanner` set and the constraints above applied; unknown id raises `ValueError`; the iX500 case keeps everything else untouched).

- [ ] **Step 1: Failing tests** in `tests/menubar/test_profiles.py`: `scanner_choices()` lists both known scanners with iX500 first and the names from `KNOWN_SCANNERS`; `profile_for_scanner(p, CANOSCAN_8800F_ID)` sets scanner, `duplex False`, `split_on_blank False` and keeps `name`, `destination`, `skip_ocr`, `skip_blank_filter`, `color_mode`, `format`, `area`; a stored resolution that the 8800F entry does not offer falls back to 300 (find a real case in `scanners.py`, or test with a monkeypatched entry), likewise for an unoffered colour mode/format/area; going back to the iX500 keeps all fields and sets only `scanner` (duplex is NOT forced back to True); unknown id raises ValueError; the function does not mutate its argument.
- [ ] **Step 2: Implement** both functions in `profiles.py` (read offered values from `get_scanner(id).entry`: keys `colorModes`, `resolutions`, `formats`, `areas`; check the actual structure in `scanners.py` first).
- [ ] **Step 3: Form.** In `profile_form.py` add a "Scanner:" label and an `NSPopUpButton` (pull-down off, items from `scanner_choices()`, selected item by id) above the destination row (shift the other rows down and enlarge `WINDOW_HEIGHT`); `set_initial_values` takes the existing profile's scanner id; `show_profile_form` passes it (default `IX500_ID` for a new profile). The popup's action disables/unchecks the split checkbox for the 8800F and restores it for the iX500 (remember the user's last state in an attribute). Replace `build_profile` so that it starts from the existing profile (`dataclasses.replace(existing, …)` with the five form fields) or a fresh `Profile(...)` for a new one, then applies `profile_for_scanner(result, chosen_id)`. Keep `show_profile_form`'s signature.
- [ ] **Step 4:** `strings.json` entry for `Scanner:` in all 7 languages (look at the entry for `Name:` for the format and languages), bump `tests/test_i18n.py`'s guard; version 0.10.6; README/AGENTS.md notes.
- [ ] **Step 5:** Full suite `.venv/bin/python -m pytest -q`; also import-check the form module headlessly if possible (`.venv/bin/python -c "import scanix500.menubar.profile_form"`) and say honestly that the dropdown itself could not be clicked without a display session. Commit `feat: choose the scanner in the profile form; editing keeps hidden fields; 0.10.6`.
