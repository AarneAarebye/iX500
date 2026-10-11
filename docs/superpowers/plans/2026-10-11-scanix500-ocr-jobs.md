# scanix500 background OCR jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Dossiary scan that asks for `ocr: true` gets the plain pages back within seconds; scanix500 then makes each PDF searchable in the background (ocrmypdf, as today) and hands it out through `GET /ocr/<job>`.

**Architecture:** Step 3 of `/Users/aarneaarebye/Projects/Paperless/Dossiary/docs/superpowers/specs/2026-10-11-background-scan-ocr-design.md` (section 3, "scanix500"); wire format in `/Users/aarneaarebye/Projects/Paperless/dossiary-scan-helper/PROTOCOL.md`, section "Background OCR" and the `fake:ocr` rows of "Fake scanner mode". A disk-backed `OcrJobQueue` (one worker thread) lives in the menu bar app's bridge; the scan runs with `--skip-ocr` when OCR is requested; the fake-scanner bridge gains `fake:ocr` with in-memory jobs so dossiary-scan-helper's conformance suite (now 51 tests) passes.

**Tech Stack:** Python 3.11+, stdlib `http.server`, pypdf, ocrmypdf (external command), pytest. Venv `.venv/` (`.venv/bin/python -m pytest -q`).

## Global Constraints

- Repo `/Users/aarneaarebye/Projects/iX500`. Baseline `.venv/bin/python -m pytest -q` → 228 passed; `scripts/run-conformance.sh /Users/aarneaarebye/Projects/Paperless/dossiary-scan-helper` → 43 OK + OCR tests skipped today, all 51 OK after this plan.
- Never bind port 8765 in tests or scripts (the owner's scanix500 runs there); never install into `/Users/aarneaarebye/Projects/iX500/.venv` from a worktree (use the worktree's own venv).
- The iX500's scanner entry gains exactly `"ocr": {"languages": ["deu", "eng", "fra", "spa", "chi_sim", "chi_tra"]}` (all installed in `/opt/homebrew/share/tessdata`).
- Request validation (PROTOCOL.md): `ocr` not a boolean, `ocrLanguages` not an array of strings → 400 (with the other type checks, before every 422); `ocr: true` on a scanner without `ocr`, or an `ocrLanguages` entry not offered → 422; without `ocr: true`, `ocrLanguages` is type-checked only. Missing or `[]` → `deu+eng`. Order is priority; repeated codes count once.
- With `ocr: true` the scan runs with OCR off (`skip_ocr=True`) and every returned file gets `"ocrJob"`; a `partial` scan's files get jobs too; an error answer has none.
- Job ids: `secrets.token_urlsafe(24)`; **a job id from a request is only used as a path component after matching `\A[A-Za-z0-9_-]{22,}\Z`**, anything else → 404 (no path traversal).
- `GET /ocr/<job>`: token required (401 first); `202 {"ok": true, "state": "queued"|"running", "position": n}`; `200 {"ok": true, "files": [ONE {name, pages, data}]}` (same name/pages as the plain file), deleting the job; `404` unknown/fetched/expired; `500` error body when no searchable PDF could be made — final, the job is deleted. Other methods → 405 `Allow: GET, OPTIONS`; `/ocr` and `/ocr/` → 404.
- Jobs run one at a time; scans may start while jobs run. Jobs and results expire 7 days after their scan; they survive an app restart (on disk under `~/Library/Application Support/scanix500/ocr-jobs/<id>/`); unfinished jobs resume in their original order.
- A page OCR can't read is kept without text (job still 200); 500 only when nothing could be made (e.g. ocrmypdf missing).
- When a job finishes, the bridge folder's safety-net copy of that file is replaced by the searchable version (best-effort).
- Hardware Scan button and menu profiles: unchanged (OCR before saving).
- Version `0.5.0` (`pyproject.toml`, `scanix500.__version__`).
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01AnMx4wtUcaKMWsQm9eXUTo
  ```

---

### Task 1: `ocr_pdf()` and the disk-backed `OcrJobQueue`

**Files:**
- Modify: `src/scanix500/pdf_builder.py`
- Create: `src/scanix500/menubar/ocr_jobs.py`, `tests/menubar/test_ocr_jobs.py`
- Test: `tests/test_pdf_builder.py`

**Interfaces:**
- Produces: `pdf_builder.ocr_pdf(input_path: Path, output_path: Path, languages: list[str]) -> None` (raises `OcrFailed` when no searchable PDF could be made); `class OcrFailed(Exception)`; `ocr_jobs.OcrJobQueue(root: Path, ocr=ocr_pdf, clock=time.time, retention=7*24*3600)` with `submit(pdf_path: Path, name: str, pages: int, languages: list[str], copy_to: str | None = None) -> str`, `fetch(job_id: str) -> tuple[int, dict]`, `run_next() -> bool` (process one waiting job synchronously; False when none), `start()` (worker thread), `stop()`; `default_ocr_jobs_path() -> Path`; `JOB_ID_RE`.

- [ ] **Step 1: Failing tests**

`tests/test_pdf_builder.py` (add; reuse the file's `_content_image()` and `subprocess.run` patch style):

```python
def test_ocr_pdf_runs_ocrmypdf_on_the_whole_file(tmp_path):
    src = tmp_path / "in.pdf"; build_pdf([_content_image(), _content_image()], src, ocr=False)
    out = tmp_path / "out.pdf"
    def fake_run(cmd, check, capture_output):
        Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())
    with patch("scanix500.pdf_builder.subprocess.run", side_effect=fake_run) as run:
        ocr_pdf(src, out, ["fra", "eng"])
    cmd = run.call_args.args[0]
    assert cmd[0] == "ocrmypdf" and cmd[cmd.index("-l") + 1] == "fra+eng" and "--deskew" in cmd
    assert len(PdfReader(str(out)).pages) == 2


def test_ocr_pdf_default_languages(tmp_path):
    src = tmp_path / "in.pdf"; build_pdf([_content_image()], src, ocr=False)
    with patch("scanix500.pdf_builder.subprocess.run", side_effect=lambda cmd, check, capture_output: Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())) as run:
        ocr_pdf(src, tmp_path / "out.pdf", [])
    assert run.call_args.args[0][run.call_args.args[0].index("-l") + 1] == "deu+eng"


def test_ocr_pdf_falls_back_page_by_page(tmp_path):
    # The whole-file run fails; each page is tried on its own and a page that
    # still fails is kept without text.
    src = tmp_path / "in.pdf"; build_pdf([_content_image(), _content_image()], src, ocr=False)
    calls = []
    def fake_run(cmd, check, capture_output):
        calls.append(cmd)
        if len(calls) in (1, 3):  # whole file, then page 2
            raise subprocess_module.CalledProcessError(1, cmd)
        Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())
    with patch("scanix500.pdf_builder.subprocess.run", side_effect=fake_run):
        ocr_pdf(src, tmp_path / "out.pdf", ["deu"])
    assert len(calls) == 3 and len(PdfReader(str(tmp_path / "out.pdf")).pages) == 2


def test_ocr_pdf_raises_when_ocrmypdf_is_missing(tmp_path):
    src = tmp_path / "in.pdf"; build_pdf([_content_image()], src, ocr=False)
    with patch("scanix500.pdf_builder.subprocess.run", side_effect=FileNotFoundError(2, "no", "ocrmypdf")):
        with pytest.raises(OcrFailed):
            ocr_pdf(src, tmp_path / "out.pdf", [])
```

`tests/menubar/test_ocr_jobs.py`:

```python
import base64
import json
from pathlib import Path

import pytest

from scanix500.menubar.ocr_jobs import JOB_ID_RE, OcrJobQueue
from scanix500.pdf_builder import OcrFailed


class Clock:
    def __init__(self): self.now = 1_000_000.0
    def __call__(self): return self.now


def fake_ocr(src, dst, languages):
    Path(dst).write_bytes(Path(src).read_bytes() + b"%SEARCHABLE " + "+".join(languages).encode())


def make(tmp_path, ocr=fake_ocr):
    clock = Clock()
    return OcrJobQueue(tmp_path / "jobs", ocr=ocr, clock=clock), clock


def plain(tmp_path, name="scan.pdf"):
    p = tmp_path / name; p.write_bytes(b"%PDF-1.4 plain"); return p


def test_job_lifecycle(tmp_path):
    q, _ = make(tmp_path)
    job = q.submit(plain(tmp_path), "scan.pdf", 2, ["deu"])
    assert JOB_ID_RE.fullmatch(job)
    assert q.fetch(job) == (202, {"ok": True, "state": "queued", "position": 0})
    assert q.run_next() is True
    status, body = q.fetch(job)
    assert status == 200 and body["ok"] is True
    [f] = body["files"]
    assert (f["name"], f["pages"]) == ("scan.pdf", 2)
    assert base64.b64decode(f["data"]).endswith(b"%SEARCHABLE deu")
    assert q.fetch(job)[0] == 404
    assert not (tmp_path / "jobs" / job).exists()


def test_order_and_position(tmp_path):
    q, clock = make(tmp_path)
    a = q.submit(plain(tmp_path, "a.pdf"), "a.pdf", 1, [])
    clock.now += 1
    b = q.submit(plain(tmp_path, "b.pdf"), "b.pdf", 1, [])
    assert q.fetch(b) == (202, {"ok": True, "state": "queued", "position": 1})
    q.run_next()
    assert q.fetch(b) == (202, {"ok": True, "state": "queued", "position": 0})
    assert q.fetch(a)[0] == 200


def test_failed_job_is_final(tmp_path):
    def broken(src, dst, languages): raise OcrFailed("ocrmypdf isn't installed")
    q, _ = make(tmp_path, ocr=broken)
    job = q.submit(plain(tmp_path), "scan.pdf", 1, [])
    q.run_next()
    status, body = q.fetch(job)
    assert status == 500 and body["ok"] is False and body["partial"] is False and "ocrmypdf" in body["message"]
    assert q.fetch(job)[0] == 404


def test_survives_a_restart_in_order(tmp_path):
    q, clock = make(tmp_path)
    a = q.submit(plain(tmp_path, "a.pdf"), "a.pdf", 1, [])
    clock.now += 1
    b = q.submit(plain(tmp_path, "b.pdf"), "b.pdf", 1, [])
    meta = tmp_path / "jobs" / a / "meta.json"
    data = json.loads(meta.read_text()); data["state"] = "running"; meta.write_text(json.dumps(data))  # crashed mid-job
    q2 = OcrJobQueue(tmp_path / "jobs", ocr=fake_ocr, clock=clock)
    assert q2.fetch(a) == (202, {"ok": True, "state": "queued", "position": 0})
    q2.run_next()
    assert q2.fetch(a)[0] == 200 and q2.fetch(b)[0] == 202


def test_expiry(tmp_path):
    q, clock = make(tmp_path)
    job = q.submit(plain(tmp_path), "scan.pdf", 1, [])
    clock.now += 7 * 24 * 3600 + 1
    assert q.fetch(job)[0] == 404
    assert not (tmp_path / "jobs" / job).exists()


@pytest.mark.parametrize("bad", ["../etc", "a", "", "x" * 21, "ab/cd" + "x" * 20, "..%2F" + "x" * 20])
def test_bad_ids_never_touch_the_disk(tmp_path, bad):
    q, _ = make(tmp_path)
    assert q.fetch(bad)[0] == 404


def test_copy_to_gets_the_searchable_version(tmp_path):
    q, _ = make(tmp_path)
    safety = plain(tmp_path, "safety.pdf")
    q.submit(safety, "safety.pdf", 1, [], copy_to=str(safety))
    q.run_next()
    assert safety.read_bytes().endswith(b"%SEARCHABLE ")


def test_worker_thread_processes_jobs(tmp_path):
    q, _ = make(tmp_path)
    q.start()
    try:
        job = q.submit(plain(tmp_path), "scan.pdf", 1, [])
        import time
        deadline = time.time() + 5
        while q.fetch(job)[0] == 202 and time.time() < deadline:
            time.sleep(0.05)
        # fetch deletes on 200, so check it arrived
        assert time.time() < deadline
    finally:
        q.stop()
```

Run: `.venv/bin/python -m pytest -q tests/menubar/test_ocr_jobs.py tests/test_pdf_builder.py` → FAIL (`ImportError`).

- [ ] **Step 2: Implement**

`src/scanix500/pdf_builder.py` — add:

```python
class OcrFailed(Exception):
    """No searchable PDF could be made (ocrmypdf missing, the input unreadable)."""


def _ocrmypdf(input_path: Path, output_path: Path, languages: str) -> None:
    subprocess.run(["ocrmypdf", "-l", languages, "--deskew", str(input_path), str(output_path)],
                   check=True, capture_output=True)


def ocr_pdf(input_path: Path, output_path: Path, languages: list[str]) -> None:
    """Makes a searchable copy of an image-only PDF. Tries the whole file
    first; if that fails, each page on its own, keeping a page OCR can't
    read without text. Raises OcrFailed when nothing could be made."""
    codes = "+".join(dict.fromkeys(languages)) or OCR_LANGUAGE
    try:
        _ocrmypdf(input_path, output_path, codes)
        return
    except FileNotFoundError as e:
        raise OcrFailed("ocrmypdf isn't installed, so the scan couldn't be made searchable.") from e
    except (subprocess.CalledProcessError, OSError):
        logger.warning("OCR of %s failed as a whole; trying page by page", input_path)
    try:
        reader = PdfReader(str(input_path))
    except Exception as e:  # noqa: BLE001
        raise OcrFailed(f"The scan's PDF couldn't be read: {e}") from e
    with tempfile.TemporaryDirectory() as tmp:
        page_paths = []
        for i, page in enumerate(reader.pages):
            single = Path(tmp) / f"page_{i}.pdf"
            writer = PdfWriter(); writer.add_page(page)
            with open(single, "wb") as f:
                writer.write(f)
            done = Path(tmp) / f"page_{i}.ocr.pdf"
            try:
                _ocrmypdf(single, done, codes)
                page_paths.append(done)
            except FileNotFoundError as e:
                raise OcrFailed("ocrmypdf isn't installed, so the scan couldn't be made searchable.") from e
            except (subprocess.CalledProcessError, OSError):
                page_paths.append(single)  # this page stays without text
        _merge_pdfs(page_paths, output_path)
```

(Import `PdfReader` from pypdf next to `PdfWriter`; `_merge_pdfs` and `OCR_LANGUAGE` already exist.)

`src/scanix500/menubar/ocr_jobs.py`:

```python
"""Background OCR jobs for version 2 scans (dossiary-scan-helper's PROTOCOL.md,
"Background OCR"): a scan answers with plain PDFs, and each file's job makes
it searchable here, one at a time, on disk so a restart doesn't lose it.

<root>/<id>/meta.json  {name, pages, languages, created_at, state, message, copy_to}
<root>/<id>/input.pdf  the plain scan
<root>/<id>/result.pdf the searchable PDF, once done

No rumps/AppKit here."""

from __future__ import annotations

import base64
import json
import logging
import re
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

from scanix500.menubar.protocol_v2 import error_body
from scanix500.pdf_builder import OcrFailed, ocr_pdf

logger = logging.getLogger(__name__)

JOB_ID_RE = re.compile(r"\A[A-Za-z0-9_-]{22,}\Z")
RETENTION_SECONDS = 7 * 24 * 3600


def default_ocr_jobs_path() -> Path:
    return Path.home() / "Library" / "Application Support" / "scanix500" / "ocr-jobs"


class OcrJobQueue:
    def __init__(self, root: Path, ocr: Callable[[Path, Path, list[str]], None] = ocr_pdf,
                 clock: Callable[[], float] = time.time, retention: float = RETENTION_SECONDS):
        self._root = root
        self._ocr = ocr
        self._clock = clock
        self._retention = retention
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._root.mkdir(parents=True, exist_ok=True)
        # A job that was running when the app quit starts again from the queue.
        for job_id, meta in self._all():
            if meta["state"] == "running":
                meta["state"] = "queued"
                self._write_meta(job_id, meta)

    # -- storage ------------------------------------------------------------
    def _dir(self, job_id: str) -> Path:
        return self._root / job_id

    def _read_meta(self, job_id: str) -> dict | None:
        try:
            return json.loads((self._dir(job_id) / "meta.json").read_text())
        except (OSError, ValueError):
            return None

    def _write_meta(self, job_id: str, meta: dict) -> None:
        tmp = self._dir(job_id) / "meta.json.tmp"
        tmp.write_text(json.dumps(meta))
        tmp.replace(self._dir(job_id) / "meta.json")

    def _all(self) -> list[tuple[str, dict]]:
        jobs = []
        for d in self._root.iterdir() if self._root.exists() else []:
            if d.is_dir() and JOB_ID_RE.fullmatch(d.name):
                meta = self._read_meta(d.name)
                if meta:
                    jobs.append((d.name, meta))
        return sorted(jobs, key=lambda j: (j[1]["created_at"], j[0]))

    def _delete(self, job_id: str) -> None:
        shutil.rmtree(self._dir(job_id), ignore_errors=True)

    def _expire(self) -> None:
        now = self._clock()
        for job_id, meta in self._all():
            if now - meta["created_at"] > self._retention:
                self._delete(job_id)

    # -- protocol -----------------------------------------------------------
    def submit(self, pdf_path: Path, name: str, pages: int, languages: list[str], copy_to: str | None = None) -> str:
        job_id = secrets.token_urlsafe(24)
        with self._lock:
            self._dir(job_id).mkdir(parents=True)
            shutil.copyfile(pdf_path, self._dir(job_id) / "input.pdf")
            self._write_meta(job_id, {"name": name, "pages": pages, "languages": list(languages),
                                      "created_at": self._clock(), "state": "queued", "message": None,
                                      "copy_to": copy_to})
            self._wake.notify_all()
        return job_id

    def fetch(self, job_id: str) -> tuple[int, dict]:
        gone = (404, error_body("This OCR job is unknown, already fetched, or expired."))
        if not JOB_ID_RE.fullmatch(job_id or ""):
            return gone
        with self._lock:
            self._expire()
            meta = self._read_meta(job_id)
            if meta is None:
                return gone
            if meta["state"] == "done":
                data = (self._dir(job_id) / "result.pdf").read_bytes()
                self._delete(job_id)
                return 200, {"ok": True, "files": [{"name": meta["name"], "pages": meta["pages"],
                                                     "data": base64.b64encode(data).decode("ascii")}]}
            if meta["state"] == "failed":
                self._delete(job_id)
                return 500, error_body(meta["message"] or "The scan couldn't be made searchable.")
            ahead = [j for j, m in self._all() if m["state"] in ("queued", "running")
                     and (m["created_at"], j) < (meta["created_at"], job_id)]
            state = "running" if meta["state"] == "running" else "queued"
            return 202, {"ok": True, "state": state, "position": len(ahead)}

    # -- work ---------------------------------------------------------------
    def run_next(self) -> bool:
        with self._lock:
            self._expire()
            waiting = [(j, m) for j, m in self._all() if m["state"] == "queued"]
            if not waiting:
                return False
            job_id, meta = waiting[0]
            meta["state"] = "running"
            self._write_meta(job_id, meta)
        src, dst = self._dir(job_id) / "input.pdf", self._dir(job_id) / "result.pdf"
        try:
            self._ocr(src, dst, meta["languages"])
            meta["state"] = "done"
        except OcrFailed as e:
            meta["state"], meta["message"] = "failed", str(e)
        except Exception as e:  # noqa: BLE001 - never kill the worker
            logger.exception("OCR job %s failed", job_id)
            meta["state"], meta["message"] = "failed", f"The scan couldn't be made searchable: {e}"
        with self._lock:
            if self._dir(job_id).exists():
                self._write_meta(job_id, meta)
        if meta["state"] == "done" and meta.get("copy_to"):
            try:
                shutil.copyfile(dst, meta["copy_to"])
            except OSError:
                pass  # the safety-net copy stays the plain scan -- not fatal
        return True

    def start(self) -> None:
        if self._thread:
            return
        def loop():
            while True:
                with self._lock:
                    if self._stopping:
                        return
                if not self.run_next():
                    with self._lock:
                        if self._stopping:
                            return
                        self._wake.wait(timeout=60)
        self._thread = threading.Thread(target=loop, daemon=True, name="scanix500-ocr-jobs")
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            self._wake.notify_all()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
```

- [ ] **Step 3: Run the tests**

`.venv/bin/python -m pytest -q` → all pass (228 + new).

- [ ] **Step 4: Commit**

```bash
git add src tests
git commit -m "OCR jobs: ocr_pdf() and a disk-backed background queue"
```

---

### Task 2: Protocol, bridge and fake mode

**Files:**
- Modify: `src/scanix500/menubar/protocol_v2.py`, `src/scanix500/menubar/bridge.py`, `src/scanix500/menubar/fakes.py`, `src/scanix500/__init__.py`, `pyproject.toml`, `README.md`, `CLAUDE.md`
- Test: `tests/menubar/test_protocol_v2.py`, `tests/menubar/test_bridge.py`, `tests/menubar/test_fakes.py`

**Interfaces:**
- Consumes: Task 1's `OcrJobQueue`, `default_ocr_jobs_path()`.
- Produces: `IX500["ocr"]`; settings gain `"ocr": bool, "ocrLanguages": list[str]`; `V2Backend.ocr_job(job_id) -> tuple[int, dict]`; `RealBackend(get_destination, trigger, ocr_jobs: OcrJobQueue | None = None)` (None → a started queue at `default_ocr_jobs_path()`); `FakeBackend` gains `fake:ocr` and in-memory jobs.

- [ ] **Step 1: Failing tests**

`tests/menubar/test_protocol_v2.py` (add; the existing `test_ix500_entry_is_exact` gains the `ocr` key):

```python
@pytest.mark.parametrize("body, status", [
    ({"scanner": "sane:ix500", "ocr": "yes"}, 400),
    ({"scanner": "sane:ix500", "ocr": True, "ocrLanguages": "deu"}, 400),
    ({"scanner": "sane:ix500", "ocr": True, "ocrLanguages": [1]}, 400),
    ({"scanner": "sane:ix500", "ocr": "yes", "ocrLanguages": ["xyz"]}, 400),
    ({"scanner": "sane:ix500", "ocr": True, "ocrLanguages": ["xyz"]}, 422),
])
def test_ocr_validation(body, status):
    assert _status(body) == status


def test_ocr_defaults_and_type_only_without_ocr():
    _, s = validate({"scanner": "sane:ix500", "ocrLanguages": ["xyz"]}, [IX500])
    assert (s["ocr"], s["ocrLanguages"]) == (False, ["xyz"])
    _, s = validate({"scanner": "sane:ix500", "ocr": True, "ocrLanguages": ["fra", "fra", "eng"]}, [IX500])
    assert (s["ocr"], s["ocrLanguages"]) == (True, ["fra", "eng"])


def test_profile_skips_ocr_when_the_job_does_it():
    settings = {"source": "feeder", "duplex": True, "colorMode": "gray", "resolution": 300,
                "format": "pdf", "extras": {}, "ocr": True, "ocrLanguages": []}
    assert profile_for(settings, "/d").skip_ocr is True
    assert profile_for({**settings, "ocr": False}, "/d").skip_ocr is False
```

`tests/menubar/test_bridge.py` (add; reuse `_request`, `v2_server` style with a `tmp_path` queue):

```python
@pytest.fixture
def ocr_server(unused_tcp_port, tmp_path):
    from scanix500.menubar.bridge import RealBackend
    from scanix500.menubar.ocr_jobs import OcrJobQueue
    pdf = tmp_path / "scan_2026-10-11_120000.pdf"
    from pypdf import PdfWriter
    w = PdfWriter(); w.add_blank_page(100, 100); w.write(str(pdf))
    trigger = FakeScanTrigger(result=ScanResult(ok=True, partial=False, message="x", output_paths=[str(pdf)]))
    def fake_ocr(src, dst, languages): Path(dst).write_bytes(Path(src).read_bytes() + b"%SEARCHABLE")
    queue = OcrJobQueue(tmp_path / "jobs", ocr=fake_ocr)
    backend = RealBackend(lambda: str(tmp_path), trigger, ocr_jobs=queue)
    server = start_bridge_server(lambda: str(tmp_path), trigger, PAIRING, port=unused_tcp_port, backend=backend)
    yield f"http://127.0.0.1:{unused_tcp_port}", trigger, queue, pdf
    server.shutdown(); server.server_close()


def test_ocr_scan_answers_with_jobs_and_skips_ocr(ocr_server):
    base, trigger, queue, pdf = ocr_server
    status, body, _ = _request(base, "POST", "/scan", {"scanner": "sane:ix500", "ocr": True, "ocrLanguages": ["fra"]})
    assert status == 200 and trigger.called_with.skip_ocr is True
    job = body["files"][0]["ocrJob"]
    assert _request(base, "GET", f"/ocr/{job}")[0] == 202
    queue.run_next()
    status, body, _ = _request(base, "GET", f"/ocr/{job}")
    assert status == 200 and len(body["files"]) == 1 and body["files"][0]["name"] == pdf.name
    assert pdf.read_bytes().endswith(b"%SEARCHABLE")  # the safety-net copy was replaced
    assert _request(base, "GET", f"/ocr/{job}")[0] == 404


def test_scan_without_ocr_has_no_jobs(ocr_server):
    base, trigger, _, _ = ocr_server
    status, body, _ = _request(base, "POST", "/scan", {"scanner": "sane:ix500"})
    assert status == 200 and "ocrJob" not in body["files"][0] and trigger.called_with.skip_ocr is False


def test_ocr_routes(ocr_server):
    base, *_ = ocr_server
    assert _request(base, "GET", "/ocr/" + "x" * 30, token=None)[0] == 401
    status, _, headers = _request(base, "POST", "/ocr/" + "x" * 30, body=b"{}", token=None)
    assert status == 405 and headers["Allow"] == "GET, OPTIONS"
    assert _request(base, "GET", "/ocr/")[0] == 404
    assert _request(base, "GET", "/ocr")[0] == 404
    assert _request(base, "GET", "/ocr/" + "x" * 30)[0] == 404
    assert _request(base, "GET", "/ocr/..%2F..%2Fetc")[0] == 404
```

`tests/menubar/test_fakes.py` (add):

```python
def test_fake_ocr_scanner_and_job():
    backend = FakeBackend()
    assert [s["id"] for s in backend.scanners()][-1] == "fake:ocr"
    scanner, settings = validate({"scanner": "fake:ocr", "ocr": True}, backend.scanners())
    status, body = backend.scan(scanner, settings)
    f = body["files"][0]
    assert b"Flatbed page 1" not in base64.b64decode(f["data"])
    assert backend.ocr_job(f["ocrJob"])[0] == 202
```

(the existing `test_seven_fakes` checks only the first seven ids, `[:7]`.)

Run: `.venv/bin/python -m pytest -q` → new tests fail.

- [ ] **Step 2: Implement**

`protocol_v2.py`:
- `IX500["ocr"] = {"languages": ["deu", "eng", "fra", "spa", "chi_sim", "chi_tra"]}` (in the literal).
- In `validate()`, with the type checks (before the 422 checks):

```python
    if "ocr" in body and not isinstance(body["ocr"], bool):
        raise ProtocolError(400, "“ocr” must be true or false.")
    languages = body.get("ocrLanguages", [])
    if not isinstance(languages, list) or not all(isinstance(lang, str) for lang in languages):
        raise ProtocolError(400, "“ocrLanguages” must be a list of language codes.")
```

  after the extras 422 loop:

```python
    ocr = body.get("ocr", False)
    offered = (scanner.get("ocr") or {}).get("languages", [])
    if ocr and not offered:
        raise ProtocolError(422, f"{name} can't make a searchable PDF.")
    if ocr:
        for lang in languages:
            if lang not in offered:
                raise ProtocolError(422, f"{name} has no OCR language “{lang}”.")
```

  and return settings with `"ocr": ocr, "ocrLanguages": list(dict.fromkeys(languages))`.
- `profile_for()`: `skip_ocr=bool(settings.get("ocr", False))`, docstring: OCR stays on unless a background job does it.

`bridge.py`:
- `ENDPOINTS` gains `"/ocr/<job>": "GET"`; in `_dispatch`, before the `ENDPOINTS` lookup, map `/ocr/<id>` (non-empty id without `/`) to that key, keeping the id: `job_id = url.path[len("/ocr/"):]`; `/ocr` and `/ocr/` stay unknown (404). After the token check: `if path == "/ocr/<job>": status, body = backend.ocr_job(job_id); self._send_json(status, body); return`.
- `V2Backend` gains `def ocr_job(self, job_id: str) -> tuple[int, dict]: ...`.
- `RealBackend.__init__(self, get_destination, trigger, ocr_jobs=None)`: `self._ocr_jobs = ocr_jobs` or, when None, `OcrJobQueue(default_ocr_jobs_path())` followed by `.start()`. `scan()`:

```python
    def scan(self, scanner: dict, settings: dict) -> tuple[int, dict]:
        # ScanBusyError propagates to the handler (409).
        result = self._trigger.trigger(profile_for(settings, self._get_destination()))
        status, body = response_from_result(result)
        if status == 200 and settings.get("ocr"):
            by_name = {Path(p).name: p for p in result.output_paths}
            for entry in body.get("files", []):
                path = by_name.get(entry["name"])
                if path:
                    entry["ocrJob"] = self._ocr_jobs.submit(Path(path), entry["name"], entry["pages"],
                                                            settings["ocrLanguages"], copy_to=path)
        return status, body

    def ocr_job(self, job_id: str) -> tuple[int, dict]:
        return self._ocr_jobs.fetch(job_id)
```

`fakes.py`: append `fake:ocr` to `FAKE_SCANNERS` (`_entry("fake:ocr", "Fake OCR Scanner", ["flatbed"])` plus `"ocr": {"languages": ["deu", "eng"]}`); port the reference helper's in-memory `OcrJobs` (`/Users/aarneaarebye/Projects/Paperless/dossiary-scan-helper/reference/scanbridge_ref/ocr_jobs.py`, 2 s delay, serial, delete on fetch, 7-day expiry) into `fakes.py` as `FakeOcrJobs`; `FakeBackend.__init__` creates one; in `FakeBackend.scan`, when `settings["ocr"]`, the immediate file is `make_pdf([""] * len(pages))` with `"ocrJob": self._ocr.submit(make_pdf(pages), name, len(pages))`; `FakeBackend.ocr_job(job_id)` returns `self._ocr.fetch(job_id)`.

Version `0.5.0` in `pyproject.toml` and `scanix500/__init__.py`. Docs: README "Protocol version 2" section and CLAUDE.md's protocol bullet — background OCR (`ocr`, jobs, `--skip-ocr`, the queue folder, safety-net copy replaced, button/profiles unchanged, `fake:ocr`).

- [ ] **Step 3: Run the tests and the conformance suite**

`.venv/bin/python -m pytest -q` → all pass. `scripts/run-conformance.sh /Users/aarneaarebye/Projects/Paperless/dossiary-scan-helper` → `Ran 51 tests … OK`, none skipped.

- [ ] **Step 4: Commit**

```bash
git add src tests pyproject.toml README.md CLAUDE.md
git commit -m "Protocol version 2: background OCR jobs (ocr, ocrJob, GET /ocr/<job>), fake:ocr"
```

---

### Task 3: Hardware check (owner present)

- [ ] Restart the menu bar app on the merged code (`./stop-menubar.sh && ./start-menubar.sh`), `curl -s http://127.0.0.1:8765/health` shows `0.5.0`.
- [ ] From Dossiary (main, with background OCR and "Make scans searchable automatically" on): scan 3 sheets two-sided → the pages arrive within roughly the scanning time (no OCR wait), the documents show "Making searchable…", and within a minute or so they become searchable (search for a word on a page) with the plain scan kept as the original.
- [ ] Quit scanix500 while a job is queued, start it again → the job finishes after the restart.
- [ ] Hardware Scan button and a menu profile still produce searchable PDFs directly.
