# Pi button scans (scanix500 0.10.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the Raspberry Pi, the CanoScan 8800F's PDF buttons collect pages into one PDF (FINISH or a 10-minute timeout closes it), and finished PDFs wait on a list that a helper can fetch over three new protocol calls.

**Architecture:** A pure `ScanCollection` (shared later with the Mac app) holds the open pages on disk. A `ButtonScanStore` holds finished PDFs, makes them searchable in one worker thread and expires them. A `ButtonCollector` thread polls the 8800F's buttons through the existing `pixma_button_overrides` and scans pages through the existing CLI run. The bridge handler gets `GET /button-scans`, `GET /button-scans/<id>`, `DELETE /button-scans/<id>` in remote mode only.

**Tech Stack:** Python 3.11+, pytest, pypdf (`PdfWriter`, already used), the existing `pdf_builder`, `bridge.py` handler.

Spec: `docs/superpowers/specs/2026-11-10-pi-button-scans-design.md` (read it first). Repo: `/Users/aarneaarebye/Projects/Paperless/iX500`. Run tests with `.venv/bin/pytest` (or the project's usual `pytest`).

## Global Constraints

- Scope of this plan: `scanix500` repo only (collection, store, collector, endpoints, status/CLI, docs). The protocol text, conformance tests and the macOS helper are a separate plan in `dossiary-scan-helper`; the Mac menu bar app is a later plan.
- Nothing under `scanix500.server` or `scanix500/collection.py` may import rumps or AppKit (`tests/server/test_runtime.py` checks the server).
- Hardware-touching code (real SANE button reads, the real scan) is verified manually on node-000, not mocked; mock only at the orchestration boundary, as `tests/menubar/test_button_watcher.py` does.
- Nothing a person scanned is ever discarded: `finish()` removes the collection folder only after the merged PDF is in place; OCR failure keeps the PDF without text; an expired collection is finished, not deleted.
- Collecting rules: buttons 4 (colour), 5 (black and white), 6 (custom) add one page; 7 finishes; 10 minutes (`collect_timeout_minutes`, default 10, minimum 1) without a press close the collection; FINISH on an empty collection does nothing.
- Protocol (remote mode only, `Authorization: Bearer` required, 401 without; any `Origin` already refused): `GET /button-scans` -> `200 {"scans":[{"id","name","pages","bytes","created","ocr"}]}` oldest first, `ocr` is `pending|done|failed`; `GET /button-scans/<id>` -> `200` `application/pdf` with `Content-Disposition: attachment; filename="<name>"` and, while OCR is pending, header `X-Scanix500-Ocr: pending`; `DELETE /button-scans/<id>` -> `204` (also `404` when gone); ids match `\A[A-Za-z0-9_-]{22,}\Z`, anything else `404`; `GET /health` adds `"buttonScans": true` in remote mode. Version 1 and 2 requests are unchanged.
- Waiting list: files kept until deleted by a client or 30 days after `created`. A page scan is refused when less than 200 MB is free on the state folder's disk (shown in `status`).
- New user-visible texts (status lines, CLI messages) go through `t()` and `src/scanix500/strings.json` in all seven languages (en, de, es, fr, zh-Hans, zh-Hant, hy; `tests/test_i18n.py` guards). Texts sent over the protocol stay English.
- Do not edit the version (`pyproject.toml`, `__init__.py`), tags or release files: the controller does the 0.10.0 release and the node-000 hardware check.

---

### Task 1: `ScanCollection`

**Files:**
- Create: `src/scanix500/collection.py`
- Create: `tests/test_collection.py`

**Interfaces:**
- Produces (later tasks use exactly this):
  ```python
  @dataclass(frozen=True)
  class CollectionSettings:
      skip_ocr: bool
      ocr_languages: list[str]
      scanner: str

  class ScanCollection:
      def __init__(self, folder: Path, clock: Callable[[], float] = time.time): ...
      page_count: int            # property
      is_open: bool              # property: page_count > 0
      settings: CollectionSettings | None   # property: None when not open
      last_activity: float | None           # property
      def add_page(self, pdf_path: Path, settings: CollectionSettings) -> int   # moves the file in, returns the new page count
      def expired(self, timeout_seconds: float) -> bool
      def finish(self, out_path: Path) -> Path    # merged PDF at out_path; folder emptied afterwards; ValueError when empty
      def discard(self) -> None
      def damaged(self) -> list[Path]             # files moved to <folder>/damaged/
  ```
  `settings` come from the first `add_page`; later calls' settings are ignored.

- [ ] **Step 1: Write the failing tests** in `tests/test_collection.py`. Make real one-page PDFs with Pillow (`Image.new("RGB", (60, 80)).save(p, "PDF")`) and read results with `pypdf.PdfReader`. Cases:
  1. empty: `page_count == 0`, `is_open` false, `settings` None, `last_activity` None, `expired(600)` false;
  2. `add_page` three PDFs: `page_count == 3`, the source files are moved away (no longer at the old path), `is_open`;
  3. `finish(out)`: `out` has 3 pages in the order added (mark pages by different sizes, e.g. 60x80, 61x80, 62x80, and compare `mediabox` widths), the collection folder holds no `page-*.pdf` and no `meta.json` afterwards, `page_count == 0`;
  4. `finish` on an empty collection raises `ValueError`; nothing is written;
  5. `finish` whose merge fails (out path inside a folder that does not exist, or a monkeypatched `_merge_pdfs` raising) leaves all pages and `meta.json` in place and `page_count` unchanged;
  6. `expired`: with a fake clock, `add_page` at t=0; `expired(600)` false at t=599, true at t=600; a later `add_page` at t=500 moves the deadline to t=1100;
  7. settings come from the first page only;
  8. `recover` by constructing a new `ScanCollection(sameFolder)` after "restart": same `page_count`, `settings`, `last_activity`;
  9. damaged: a `page-002.pdf` that is not a PDF (bytes `b"nope"`) is moved to `damaged/` on construction, `page_count` counts only valid pages, `damaged()` lists it, and a new `add_page` still works and numbering continues without clashing; a `meta.json` containing garbage is moved to `damaged/` too and the pages are kept with default settings (`CollectionSettings(skip_ocr=False, ocr_languages=["deu","eng"], scanner="sane:canoscan-8800f")`);
  10. `discard()` removes pages and meta; `page_count == 0`.
- [ ] **Step 2: Run** `pytest tests/test_collection.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `collection.py`: `from scanix500.pdf_builder import _merge_pdfs`; pages named `page-001.pdf`…; `meta.json` = `{"first_activity", "last_activity", "settings": {"skip_ocr", "ocr_languages", "scanner"}}` written atomically (`tempfile.mkstemp` in the folder + `os.replace`, as `profiles.save_profiles` does); validity check on load: read the file's first 5 bytes equal `%PDF-` and `pypdf.PdfReader(...).pages` has at least one page, else move to `damaged/` (`mkdir(exist_ok=True)`, a name clash gets a numeric suffix); the next page number is `max(existing numbers) + 1` so a damaged gap never clashes; `add_page` uses `shutil.move`; `finish` merges into `out_path` via `_merge_pdfs(pages, out_path, title=out_path.stem)` (write to `out_path.with_suffix(".tmp")` then `os.replace` so a half file never appears), then deletes the pages and `meta.json` only after the replace succeeded; `finish` errors other than the empty `ValueError` propagate with the collection untouched.
- [ ] **Step 4: Run** the new tests then `pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git add src/scanix500/collection.py tests/test_collection.py && git commit -m "feat: ScanCollection keeps collected pages on disk until they are finished"` (end with the attribution lines the session requires).

---

### Task 2: `ButtonScanStore`

**Files:**
- Create: `src/scanix500/server/button_scans.py`
- Create: `tests/server/test_button_scans.py`

**Interfaces:**
- Consumes: `pdf_builder.ocr_pdf(input_path, output_path, languages)` (raises `OcrFailed`), `pypdf`.
- Produces:
  ```python
  ID_RE = re.compile(r"\A[A-Za-z0-9_-]{22,}\Z")
  RETENTION_SECONDS = 30 * 24 * 3600

  class ButtonScanStore:
      def __init__(self, root: Path, ocr: Callable[[Path, Path, list[str]], None] = ocr_pdf,
                   clock: Callable[[], float] = time.time, retention: float = RETENTION_SECONDS): ...
      def add(self, pdf_path: Path, name: str, pages: int, *, skip_ocr: bool, languages: list[str]) -> str   # moves the PDF in, returns the id
      def list(self) -> list[dict]        # [{"id","name","pages","bytes","created","ocr"}], oldest first; also expires old ones
      def get(self, scan_id: str) -> tuple[Path, dict] | None   # PDF path and its list entry; None for unknown or malformed id
      def delete(self, scan_id: str) -> bool     # False when unknown
      def start(self) -> None     # worker thread: OCRs pending scans one at a time, resumes pending ones after a restart
      def stop(self) -> None
      def run_next(self) -> bool  # one pending OCR job synchronously (the worker calls it; tests call it directly)
      @property
      def waiting(self) -> int
  ```
  Files: `<root>/<id>.pdf` and `<root>/<id>.json` (`{"name","pages","created","ocr","languages"}`; `ocr` is `pending|done|failed`; `skip_ocr` makes it `done` at once). `created` is an ISO-8601 UTC string from the clock. `bytes` is the PDF's current size.

- [ ] **Step 1: Write the failing tests** (fake `ocr` that copies input to output and appends nothing, or raises `OcrFailed`; a fake clock; real tiny PDFs):
  1. `add` returns an id matching `ID_RE`; `list()` has one entry with the given name/pages, `ocr == "pending"`, correct `bytes`; with `skip_ocr=True` the entry is `done` and no job pends;
  2. `run_next()` OCRs the pending scan: the fake `ocr` is called once with the languages, the PDF is replaced by its output (compare bytes), `ocr == "done"`, `bytes` updated; a second `run_next()` returns False;
  3. a failing `ocr` (`OcrFailed`) leaves the original PDF unchanged and `ocr == "failed"`; any other exception also gives `failed` and the worker survives;
  4. `get(id)` returns the path and entry; `get` of an unknown id, of `"../x"` and of a too short id returns None; nothing outside `root` is ever touched;
  5. `delete(id)` removes both files, a second delete returns False;
  6. oldest first ordering with two scans added at different fake times;
  7. expiry: with `retention=100`, a scan older than 100 s is removed by `list()`;
  8. restart: a new `ButtonScanStore(root)` lists the same scans and `run_next()` resumes a scan that was `pending` (a scan stuck `running`, if you keep such a state, is treated as pending);
  9. `add` is atomic for listeners: a scan never appears in `list()` with a missing PDF (write the PDF into place before the JSON; `list()` ignores a JSON without its PDF and a PDF without its JSON);
  10. `start()`/`stop()` run the worker (use a `threading.Event` in the fake `ocr`).
- [ ] **Step 2: Run** `pytest tests/server/test_button_scans.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** it. Ids from `secrets.token_urlsafe(16)` (22 characters; check it matches `ID_RE`). One `threading.Lock` around metadata reads and writes; the worker loop waits on a `threading.Condition` with a 5 s timeout; OCR writes to `<id>.ocr.tmp` and `os.replace`s over the PDF only on success; JSON written atomically.
- [ ] **Step 4: Run** the new tests then `pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git add src/scanix500/server/button_scans.py tests/server/test_button_scans.py && git commit -m "feat: ButtonScanStore keeps finished button scans for pickup"` (with the attribution lines).

---

### Task 3: Finish button, config keys

**Files:**
- Modify: `src/scanix500/menubar/button_watcher.py` (`PIXMA_BUTTONS`, `_pixma_overrides`, `pixma_button_overrides`)
- Modify: `src/scanix500/server/config.py`
- Modify: `tests/menubar/test_button_watcher.py`, create `tests/server/test_config.py` (or extend the existing config tests if one exists)

**Interfaces:**
- Produces:
  ```python
  FINISH = "finish"   # in button_watcher.py
  # _pixma_overrides(values) -> dict | str | None
  #   target 4/5/6 -> overrides dict as today ({"color_mode":"color"}, {"color_mode":"bw"}, {})
  #   target 7 -> FINISH
  #   anything else (0, 1, 2, 3, missing) -> None
  # ServerConfig gains: collect_timeout_minutes: int = 10,
  #   button_custom_mode: str = "gray", button_custom_resolution: int = 300
  #   and properties: collection_path (state/collection), button_scans_path (state/button-scans)
  ```
  `pixma_button_overrides(device_name)` keeps its signature and may now return `FINISH`. The Mac app (`app.py`) calls `_on_buttons_read(hit)` with `(scanner_id, overrides)`: it must keep working, so in this task make `_on_buttons_read` ignore a `FINISH` hit for now (a one-line guard `if overrides == FINISH: return`) and add a test for that guard; the Mac app plan gives FINISH its meaning later.

- [ ] **Step 1: Failing tests:** the table test for `_pixma_overrides` gains target 7 -> `FINISH` (and its copy test still holds for dicts); `_on_buttons_read` with a FINISH hit starts no scan (use the existing app test pattern with `ScanixMenuBarApp.__new__`); config: keys `collect_timeout_minutes`, `button_custom_mode` (`gray|color|bw`), `button_custom_resolution` (150, 300, 600, 1200) are read from the `[server]` section; defaults when absent; a value below 1, not a number, an unknown mode or resolution raises `ValueError` with a message naming the key and the file, like `port` does; the two new folder properties.
- [ ] **Step 2: Run** the new tests. Expected: FAIL.
- [ ] **Step 3: Implement.** `button_custom_*` map to the CUSTOM button's `Profile` overrides in Task 4; here only parse and validate them.
- [ ] **Step 4: Run** `pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git add src tests && git commit -m "feat: the 8800F's FINISH button is read; server config for collecting"` (with the attribution lines).

---

### Task 4: `ButtonCollector` and the server wiring

**Files:**
- Create: `src/scanix500/server/buttons.py`
- Modify: `src/scanix500/server/cli.py` (`cmd_run`, `cmd_status`, a new `button-scans` command)
- Modify: `src/scanix500/server/runtime.py` only if the trigger needs a shared lock accessor (keep changes minimal)
- Create: `tests/server/test_buttons.py`; extend `tests/server/test_cli.py`

**Interfaces:**
- Consumes: Tasks 1-3; `ServerScanTrigger.busy` and `.trigger(profile)`; `button_watcher.BUTTON_READERS`/`pixma_button_overrides`/`resolve_device_name`; `scanners.usb_ids()` and `scanners.CANOSCAN_8800F_ID`; `profiles.Profile`; `runner.ScanResult`.
- Produces:
  ```python
  class ButtonCollector:
      def __init__(self, collection: ScanCollection, store: ButtonScanStore, trigger: ServerScanTrigger,
                   cfg: ServerConfig, *, read: Callable[[str], dict | str | None] = pixma_button_overrides,
                   resolve: Callable[[str], str | None] = resolve_device_name,
                   usb: Callable[[], set] = usb_ids, free_bytes: Callable[[Path], int] = ...,
                   clock: Callable[[], float] = time.time, interval: float = 1.5): ...
      def poll_once(self) -> None      # one tick: read the buttons, act, apply the timeout
      def start(self) -> None
      def stop(self) -> None
      def finish_now(self) -> Path | None   # what FINISH and the timeout call; None when empty
      def status_lines(self) -> list[str]   # for the log; the CLI reads the files itself
  ```
  Behaviour of `poll_once`: (1) if the collection `expired(timeout)` and no scan runs: `finish_now()`; (2) if `trigger.busy` or the 8800F's USB id is not in `usb()`: return without opening SANE; (3) resolve the device once and cache it (forget it on any exception, like `ButtonWatcher`); (4) `hit = read(device)`; `FINISH` -> `finish_now()`; a dict -> `scan_page(overrides)`; None -> nothing. `scan_page` refuses when `free_bytes(state) < 200 MB` (logs a warning, adds no page); builds `Profile(name="Button", destination=<tmp incoming folder>, skip_ocr=True, scanner=CANOSCAN_8800F_ID, color_mode=..., resolution=..., format="pdf")` from the overrides (colour 300 dpi; `{}` = CUSTOM = `cfg.button_custom_mode`/`button_custom_resolution`); runs it through `trigger.trigger(profile)` (so it shares the one-scan-at-a-time lock; a `ScanBusyError` means skip this tick); on `ok` and exactly one output path, `collection.add_page(path, CollectionSettings(skip_ocr=False, ocr_languages=["deu","eng"], scanner=CANOSCAN_8800F_ID))`; on failure logs it and adds nothing. `finish_now` runs `collection.finish(<tmp>/scan_<YYYY-MM-DD>_<HHMMSS>.pdf)` then `store.add(path, name, pages, skip_ocr=settings.skip_ocr, languages=settings.ocr_languages)`. `finish_now` also takes the trigger lock while merging so a scan does not start mid-merge (non-blocking: if busy, try again next tick).

- [ ] **Step 1: Failing tests** with fakes (a fake `trigger` that records profiles and returns `ScanResult(ok=True, ..., output_paths=[<a real tiny PDF it created>])`, fake `read` returning a scripted sequence, fake clock, `tmp_path`):
  1. press 4, 5, 6 -> three scans run with `color_mode` colour/bw/custom-from-config, `skip_ocr True`, scanner id, one page added each;
  2. press 7 after pages: one PDF in the store with the right page count and `name` like `scan_2026-11-10_151030.pdf`; collection empty; the collection folder is clean;
  3. press 7 with nothing collected: nothing added;
  4. timeout: after 600 s of fake clock without presses, the next `poll_once` finishes an open collection; before it, not; a press resets it;
  5. the 8800F not on the USB bus: `read` is never called;
  6. `trigger.busy` true: `read` not called;
  7. a failed scan (`ok=False`) adds no page and keeps earlier pages; the next tick can scan again;
  8. disk guard: `free_bytes` below 200 MB adds no page;
  9. an exception from `read` forgets the cached device and `poll_once` does not raise out of the thread loop (the loop catches);
  10. restart: a new collector over the same collection folder continues with the collected pages and finishes them on FINISH;
  11. `finish_now` while `trigger.busy`: returns None, nothing lost, tried again next tick.
  CLI tests: `scanix500-server status` prints "Button scans: collecting N pages (closes in M min)" or "Button scans: nothing collected" and "Waiting for pickup: N" (state read from the files, with a `SCANIX500_STATE_DIR` temp folder and permission-error handling like the other lines); `scanix500-server button-scans list` prints one line per waiting PDF; `button-scans discard-open` asks (reads `y` from stdin unless `--yes`) and removes the open collection's pages; with a `--fake-scanners` run the collector is not started.
- [ ] **Step 2: Run** the new tests. Expected: FAIL.
- [ ] **Step 3: Implement** `buttons.py`; in `cmd_run` (non-fake): create `ScanCollection(cfg.collection_path)`, `ButtonScanStore(cfg.button_scans_path)`, `store.start()`, `ButtonCollector(...).start()`, stop both on shutdown; pass the store to `start_remote_server` for Task 5 (add a `button_scans: ButtonScanStore | None = None` parameter now, unused until Task 5). `cmd_status` and `button-scans` commands read `cfg.collection_path`/`cfg.button_scans_path` read-only (construct `ScanCollection` only for reading when the folder exists; do not start threads).
- [ ] **Step 4: Add the texts** to `strings.json` (7 languages) for the new status lines and CLI prompts; run `pytest tests/test_i18n.py -q`.
- [ ] **Step 5: Run** `pytest -q`. Expected: all pass.
- [ ] **Step 6: Commit** `git add src tests && git commit -m "feat: scanix500-server collects the 8800F's button scans"` (with the attribution lines).

---

### Task 5: The three endpoints and `/health`

**Files:**
- Modify: `src/scanix500/menubar/bridge.py` (`make_handler_class`, `ENDPOINTS`/path routing, `/health`), `src/scanix500/server/runtime.py` (`start_remote_server` passes the store)
- Modify: `src/scanix500/menubar/fakes.py` only if the fake server mode needs a fixed list (see Step 3)
- Modify/Create: `tests/menubar/test_bridge.py` additions or `tests/server/test_button_scan_endpoints.py`

**Interfaces:**
- Consumes: `ButtonScanStore` (`list`, `get`, `delete`, `ID_RE`).
- `make_handler_class(..., remote=None, button_scans: ButtonScanStore | None = None)`; the routes exist only when `remote is not None and button_scans is not None`; otherwise those paths are `404` as today.
- `/health` in remote mode adds `"buttonScans": true` when a store is given.

- [ ] **Step 1: Failing tests** (start a real handler on an ephemeral port as `tests/menubar/test_bridge.py` does for remote mode, with a `ButtonScanStore` over `tmp_path` holding two tiny PDFs): list is `200`, oldest first, fields as in the Global Constraints, with `ocr` states; no token -> 401 for all three; `GET /button-scans/<id>` -> 200, `Content-Type: application/pdf`, `Content-Disposition` with the name (quotes and non-ASCII characters in the name are replaced so the header stays valid), body equals the file, `X-Scanix500-Ocr: pending` only while pending; unknown id, `../etc/passwd`, a too short id -> 404 with `error_body`; `DELETE` -> 204 and the next `GET` 404, a second `DELETE` 404; wrong methods (`POST /button-scans`) -> 405 with `Allow`; a request with an `Origin` header -> 403 (existing rule still holds for the new paths); `/health` has `buttonScans` true in remote mode and not in local mode; in local (non-remote) mode `/button-scans` is 404; all existing bridge tests still pass.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** in `_dispatch`: parse `/button-scans` and `/button-scans/<id>` (like `/ocr/<job>`; the id segment must have no `/`); `ENDPOINTS` entries for method checks (`GET` for the list and the item, plus `DELETE` for the item — the item path allows two methods, so check it separately and answer 405 `Allow: GET, DELETE, OPTIONS`); token check as for `/scanners`; the PDF is streamed from disk with `Content-Length`; the handler never reads outside the store (`get` validates the id). `start_remote_server` gets the store and passes it. For the conformance suite (later plan): when `--fake-scanners` runs, build a `ButtonScanStore` over a temp folder with two fixed PDFs ("fake-1.pdf" done, "fake-2.pdf" pending) so the helper repo's conformance tests have something to fetch; document this in `fakes.py`.
- [ ] **Step 4: Run** `pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git add src tests && git commit -m "feat: the server lists, serves and deletes finished button scans"` (with the attribution lines).

---

### Task 6: Docs

**Files:**
- Modify: `README.md` (the "Canon CanoScan 8800F" section: replace "Its buttons do nothing there" for the server; add the collecting behaviour, `server.conf` keys, `status`/`button-scans`, the waiting list, and a manual hardware checklist), `docs/raspberry-pi.md` (the new `server.conf` keys and where PDFs wait), `AGENTS.md` (the 0.10.0 bullet next to the 8800F one: `ScanCollection`, `ButtonScanStore`, `ButtonCollector`, the three endpoints, the 30-day and 200 MB rules, the test files)

- [ ] **Step 1:** Write the README checklist items (unchecked) for the controller's hardware run on node-000: three presses of COLOR, BLACK & WHITE, CUSTOM and FINISH give one 3-page PDF on `GET /button-scans` with right modes and order; the timeout closes a 2-page collection; restarting the service with 2 pages collected keeps them; `status` shows the collection and the waiting PDFs; hot-plug warning unchanged.
- [ ] **Step 2:** Make the README's server statement true: "On `scanix500-server` its buttons collect pages ... the waiting PDFs are fetched by Dossiary Scan Helper 0.6.0 or later".
- [ ] **Step 3:** `pytest -q` (docs tests, if any) and commit `git add README.md docs AGENTS.md && git commit -m "docs: button scans on the Pi"` (with the attribution lines).
