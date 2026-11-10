# Mac menu bar app: collect pages with the 8800F's buttons — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** On the Mac, the CanoScan 8800F's COLOR / BLACK & WHITE / CUSTOM buttons each scan one page into an open collection; FINISH (or 10 minutes without activity) merges the pages into one PDF in the Hardware Button profile's folder, with OCR over the whole PDF. Spec: `docs/superpowers/specs/2026-11-10-8800f-multipage-pdf-buttons-design.md` (this plan refines it where the Pi build taught us something; see "Decisions" below).

**Architecture:** The behaviour lives in a new, rumps-free module `src/scanix500/menubar/collecting.py` (`MacCollector`, unit-tested with fakes) on top of the shared `ScanCollection` (`src/scanix500/collection.py`, built for the Pi). `menubar/app.py` only wires it: the button route, the busy guard, a 15 s timer, menu items, the icon/title, notifications.

**Tech Stack:** Python 3.11, rumps/PyObjC (app.py), pypdf, ocrmypdf via `pdf_builder.ocr_pdf`, pytest (`.venv/bin/python -m pytest`).

## Decisions (agreed 2026-11-10 in the spec; refinements marked *new*)

- Only the 8800F's buttons, only while the "Hardware Button" profile's `format` is `pdf`. For `jpeg`/`png`, the iX500's feeder button, menu clicks and Dossiary scans nothing changes.
- A page is scanned without OCR into a scratch folder, then moved into the collection (`ScanCollection.add_page`). FINISH → `ScanCollection.finish(<destination>/scan_<YYYY-MM-DD>_<HHMMSS>.pdf)`, then ONE OCR run over the merged file unless the profile's `skip_ocr` is set. OCR failure keeps the merged PDF without text and says so. A failed merge or missing destination keeps the collection open.
- Timeout: `collect_timeout_minutes` (default 10, minimum 1) from `collection.json` next to `bridge_destination.txt`; nothing is ever discarded by the timeout.
- *new* `CollectionSettings` gets `destination: str = ""` (the Pi leaves it empty; old `meta.json` files without it load as `""`). The delivery settings (destination, skip_ocr, scanner) are fixed at the first page of a collection.
- *new* While a collection is open the menu bar shows the collecting icon (`icons/collecting.png`, shipped in 0.10.3, unused until now) with the page count as title text; scanning and finishing show the scanning icon.
- Menu while open: "Finish now (N pages)" and "Discard collected pages…" (confirmation first).
- Notifications: "Collecting pages. Press FINISH when you are done." after the first page of a collection; "N pages scanned" with the file name after finishing; failures as today.

## Global Constraints

- `scanix500.collection` and `scanix500.menubar.collecting` must not import rumps/AppKit.
- Nothing slow on the main thread: page scans, merge and OCR run on worker threads; UI updates via `AppHelper.callAfter`.
- One busy guard: while a scan (menu, Dossiary, button page) or a finish runs, `_scanning` is True and a second button press is ignored (as today).
- The Pi server keeps working: `ScanCollection`'s public API stays backward compatible; `tests/server` and `tests/test_collection.py` stay green.
- User-visible text via `t()` with entries in `strings.json` in all 7 languages (`tests/test_i18n.py` guards it; bump its size guard).
- Version 0.11.0 (`pyproject.toml`, `src/scanix500/__init__.py`, version strings in `tests/menubar/test_bridge.py`, README install example).
- Commit messages end with: Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com> and Claude-Session: https://claude.ai/code/session_0117epTz6pLGF3vCWxi4JMsL

---

### Task 1: `CollectionSettings.destination`

**Files:** Modify `src/scanix500/collection.py`; Test `tests/test_collection.py`.

**Interfaces:** Produces `CollectionSettings(skip_ocr, ocr_languages, scanner, destination="")`. `meta.json` stores `destination`; `_recover` reads `s.get("destination", "")`.

- [ ] **Step 1: Failing tests:** (a) a `CollectionSettings` built without `destination` has `destination == ""`; (b) after `add_page(..., CollectionSettings(..., destination="/x"))` a new `ScanCollection(folder)` on the same folder recovers `settings.destination == "/x"`; (c) a hand-written old-format `meta.json` (settings without `destination`) recovers with `""`; (d) all existing tests still pass.
- [ ] **Step 2:** Add the field (frozen dataclass, default `""`, last position), write it through `asdict`, read it with `.get`. `_DEFAULT_SETTINGS` unchanged.
- [ ] **Step 3:** `.venv/bin/python -m pytest tests/test_collection.py tests/server -q` → green. **Commit** `feat: collections remember their destination folder`.

---

### Task 2: `MacCollector` (the behaviour, rumps-free)

**Files:** Create `src/scanix500/menubar/collecting.py`; Test `tests/menubar/test_collecting.py`.

**Interfaces:**
- Consumes: `ScanCollection`, `CollectionSettings` (Task 1), `Profile`, `run_scan`/`ScanResult` (`menubar/runner.py`), `ocr_pdf`/`OcrFailed` (`pdf_builder.py`), `OCR_LANGUAGE` (`"deu+eng"`; languages list = `["deu", "eng"]`).
- Produces:

```python
@dataclass(frozen=True)
class PageOutcome:
    ok: bool                 # a page was added
    pages: int               # pages in the collection afterwards
    first_page: bool         # this page opened a new collection
    message: str             # the scan's message on failure ("" on success)

@dataclass(frozen=True)
class FinishOutcome:
    path: Path | None        # the delivered PDF, None on failure
    pages: int
    searchable: bool         # OCR done (False when skipped by profile or failed)
    message: str             # failure text ("" on success)

class MacCollector:
    def __init__(self, folder: Path, scratch: Path, *, timeout_seconds: float = 600.0,
                 run=run_scan, ocr=ocr_pdf, clock=time.time): ...
    page_count: int   # property
    is_open: bool     # property
    def expired(self) -> bool
    def scan_page(self, profile: Profile, overrides: dict) -> PageOutcome   # blocking; worker thread
    def finish(self) -> FinishOutcome                                       # blocking; worker thread
    def discard(self) -> None

def load_collect_timeout(path: Path) -> int   # minutes from {"collect_timeout_minutes": n}; default 10; < 1 or invalid -> 10; never raises
def default_collection_config_path() -> Path  # ~/Library/Application Support/scanix500/collection.json
def default_collection_folder() -> Path       # ~/Library/Application Support/scanix500/collection
```

- [ ] **Step 1: Failing tests** (fakes: `run` writes a one-page PDF made with PIL into the destination it is given and returns `ScanResult(ok=True, partial=False, message="", output_paths=[path])`; `ocr` copies input to output and records the call; a fake clock):
  1. `scan_page` runs the scan with a profile copy whose `destination` is a fresh subfolder of `scratch`, `skip_ocr=True`, `format="pdf"`, `scanner` and the button `overrides` applied (`dataclasses.replace(profile, **overrides)`); the page lands in the collection (`page_count == 1`), `first_page` True, the scratch subfolder is gone; a second call gives `pages == 2`, `first_page` False.
  2. The first page stores `CollectionSettings(destination=profile.destination, skip_ocr=profile.skip_ocr, ocr_languages=["deu","eng"], scanner=profile.scanner)`; changing the profile afterwards does not change an open collection's destination (finish uses the stored one).
  3. A failed scan (`ok=False`) adds nothing, returns `ok=False` with the message, leaves collected pages untouched and cleans the scratch folder; a scan result without a usable PDF (no file, or a non-PDF file) is an `ok=False` outcome and the stray file is kept under `scratch/kept/`, never deleted.
  4. `finish()` with 3 pages writes ONE `scan_<date>_<time>.pdf` (name from the clock) into the stored destination, 3 pages in order, OCR called exactly once with the languages, `searchable True`, the collection is empty afterwards; name collisions get `_2`, `_3`.
  5. `skip_ocr` in the stored settings → OCR not called, `searchable False`, no error text.
  6. OCR raising `OcrFailed` → the merged PDF stays (without text), `path` set, `searchable False`, `message` explains; the collection is cleared (the PDF is in place).
  7. Missing/unwritable destination → `finish()` returns `path None` with a message and the collection stays open with all pages (a later `finish` after creating the folder succeeds).
  8. `finish()` on an empty collection → `path None`, nothing created.
  9. `expired()` honours `timeout_seconds` and `last_activity` via the fake clock; an empty collection is never expired.
  10. A new `MacCollector` on the same folder (a "restart") has the pages and settings (`page_count`, `expired()` counted from the stored `last_activity`).
  11. `discard()` removes everything.
  12. `load_collect_timeout`: missing file → 10; `{"collect_timeout_minutes": 3}` → 3; 0, -1, "x", a float, broken JSON → 10.
- [ ] **Step 2: Implement** `collecting.py`. `scan_page`: make `scratch / secrets.token_hex(8)`, `run(replace(profile, **overrides, destination=<that>, skip_ocr=True, format="pdf"))`; collect `result.output_paths` that are files plus any `*.pdf` in the folder (the Pi's `_take_pages` in `server/buttons.py` is the model: valid single-page PDFs go to `collection.add_page`, anything else is moved to `scratch/kept/` with a random prefix); remove the scratch folder in `finally`; wrap run exceptions into an `ok=False` outcome. `finish()`: stored settings → destination; `out = destination / name`; `ScanCollection.finish(out)` (raises if the folder is missing: catch `Exception`, return failure, collection untouched because `finish` only clears after success); then OCR: `ocr(out, tmp, languages)` with `tmp = out.with_suffix(".ocr.tmp")`, `os.replace(tmp, out)` on success, `tmp.unlink(missing_ok=True)` on failure. A lock (`threading.Lock`) serialises `scan_page`/`finish`/`discard`.
- [ ] **Step 3:** Run the new tests and the full suite; **Commit** `feat: MacCollector collects 8800F button pages and finishes them into one PDF`.

---

### Task 3: App wiring (button route, timer, menu, icon, strings)

**Files:** Modify `src/scanix500/menubar/app.py`, `strings.json`, `tests/test_i18n.py`; Test `tests/menubar/test_app.py` (follow its `ScanixMenuBarApp.__new__` + patched collaborators style).

**Interfaces:**
- Consumes: Task 2's `MacCollector`, `FinishOutcome`, `PageOutcome`; existing `_start_scan`, `_show(state)`, `_rebuild_menu`, `FINISH`, `BUTTON_READERS`, `find_profile`, `HARDWARE_BUTTON_PROFILE_NAME`, `icon_path`.
- Produces: `self._collector: MacCollector`, `_collecting_profile(scanner_id, profile) -> bool` (True when 8800F and `profile.format == "pdf"`), `_start_page_scan(profile, overrides)`, `_start_finish()`, `_collect_tick(_sender)`, `_finish_clicked(_sender)`, `_discard_clicked(_sender)`, `_show("collecting")`.

- [ ] **Step 1: Failing tests** (patch `MacCollector` with a fake recording calls; patch `AppHelper.callAfter` to run immediately; patch `threading.Thread` to run the target synchronously where the existing tests do):
  1. `_on_buttons_read` with an 8800F hit and a pdf Hardware Button profile calls `_start_page_scan` (not `_start_scan`); with `format="jpeg"` or `"png"` it uses the old `_start_scan` path; an iX500 hit uses the old path; `FINISH` from the 8800F calls `_start_finish`; `FINISH` from the iX500 does nothing.
  2. While `_scanning` is True neither page scan nor finish starts (press ignored).
  3. After a page scan the app shows `collecting` (icon `collecting`, title = page count as text) and rebuilds the menu; the first page of a collection sends the "Collecting pages…" notification, later pages none; a failed page scan notifies the failure (as `_on_scan_complete` does today) and returns to `collecting` if pages exist, else `idle`; `_scanning` is False again in every case.
  4. `_start_finish` shows `scanning` during the work, then `idle`, rebuilds the menu, notifies "N pages scanned" with the file name; a failed finish notifies the message and the state returns to `collecting`; with `searchable False` because OCR failed the notification says so.
  5. `_collect_tick` starts a finish only when `collector.expired()` is True and nothing is scanning.
  6. At start-up with a recovered open collection (`collector.page_count == 2`) the app shows `collecting` and the menu contains the two collection items.
  7. Menu: with an open collection `_rebuild_menu` contains "Finish now (3 pages)" and "Discard collected pages…" (use the `t()` strings); without, neither. "Discard…" asks for confirmation (patch `rumps.alert`) and only discards on OK.
  8. Fallback: with `icon_path("collecting")` returning None the title shows the emoji plus the count (reuse `_show`'s fallback pattern).
- [ ] **Step 2: Implement.**
  - `__init__`: `self._collector = MacCollector(default_collection_folder(), scratch_folder, timeout_seconds=load_collect_timeout(default_collection_config_path()) * 60)` where `scratch_folder = <app support>/collection-tmp`; start `rumps.Timer(self._collect_tick, 15).start()` next to the existing button timer; after the first `_rebuild_menu`, `self._show("collecting" if self._collector.is_open else "idle")`. The collector must not be built if the folder cannot be created: wrap in try/except, log, and run without collecting (the old behaviour) — `self._collector = None` and every use guards for None.
  - `_on_buttons_read`: keep the early `hit is None` return. For `scanner_id == CANOSCAN_8800F_ID` and a `Hardware Button` profile with `format == "pdf"` and a non-None collector: `FINISH` → `self._start_finish()`; otherwise `self._start_page_scan(profile, overrides)`. All other cases exactly as before (FINISH from other scanners/formats is ignored).
  - `_start_page_scan`: busy guard like `_start_scan` (`if self._scanning: return False; self._scanning = True; self._show("scanning")`), worker thread runs `outcome = self._collector.scan_page(profile, overrides)` and `AppHelper.callAfter(self._on_page_scanned, outcome)`; `_on_page_scanned`: `_scanning = False`, notifications per test 3, `_show(...)`, `_rebuild_menu()`. Exceptions in the worker become an `ok=False` outcome (never leave `_scanning` stuck).
  - `_start_finish` / `_collect_tick` / `_finish_clicked` / `_discard_clicked` likewise (finish on a worker thread under the same guard; `_discard_clicked` is main-thread: confirm with `rumps.alert(title=..., message=..., ok=..., cancel=...)`, discard, `_show`, `_rebuild_menu`).
  - `_show(state)`: add `collecting`: icon `icon_path("collecting")` and `self.title = str(count)` (rumps shows icon plus title text); other states keep an empty title; fallback to emoji + count.
  - `_rebuild_menu`: when `self._collector and self._collector.is_open`, add the two items right below the status line (above the profile items) with a separator.
  - Strings (all 7 languages, bump the guard): "Finish now ({count} pages)", "Discard collected pages…", "Collecting pages. Press FINISH when you are done.", "{count} pages scanned: {name}", "Discard {count} collected pages?", "The pages cannot be restored.", "Discard", "The scan is finished, but the text could not be recognised." (look at how existing entries and `t(..., count=…)` formatting are written; follow them; one page uses the same plural-neutral wording or a separate "1 page" string — choose consistently and test it).
- [ ] **Step 3:** Full suite green; **Commit** `feat: the menu bar app collects 8800F button pages into one PDF`.

---

### Task 4: Docs, hardware checklist, version 0.11.0

**Files:** Modify `README.md`, `AGENTS.md`, `docs/…` (wherever the 8800F section and the manual checklist live — `grep -n "FINISH" README.md docs/*.md`), version files.

- [ ] **Step 1:** README: the 8800F section describes collecting on the Mac and on the Pi (the statement "FINISH … do nothing" no longer applies anywhere); the `collection.json` setting; the new menu items. AGENTS.md: a bullet for `MacCollector`/`collecting.py` and the `CollectionSettings.destination` field.
- [ ] **Step 2:** README manual hardware checklist (to be run by the user): three presses (COLOR, B&W, CUSTOM) and FINISH give ONE 3-page PDF in the Hardware Button folder with the right modes and a text layer; two pages collected then 10 minutes idle close the collection (with `collect_timeout_minutes` set to 1 for the test); quit the app with two pages collected and start it again: the count shows and FINISH delivers them; "Finish now" and "Discard…" in the menu; jpeg/png Hardware Button profile keeps the old one-scan behaviour; the iX500's Scan button is unchanged.
- [ ] **Step 3:** Version 0.11.0 everywhere listed in Global Constraints; full suite; **Commit** `docs: Mac page collecting; 0.11.0`.
