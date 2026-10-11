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
