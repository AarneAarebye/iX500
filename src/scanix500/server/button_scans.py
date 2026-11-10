"""The waiting list of finished button scans: PDFs kept on disk until a
client fetches and deletes them (or 30 days pass). One worker thread makes
them searchable."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import secrets
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from scanix500.pdf_builder import ocr_pdf

logger = logging.getLogger(__name__)

ID_RE = re.compile(r"\A[A-Za-z0-9_-]{22,}\Z")
RETENTION_SECONDS = 30 * 24 * 3600
WORKER_WAIT_SECONDS = 5


class ButtonScanStore:
    def __init__(self, root: Path, ocr: Callable[[Path, Path, list[str]], None] = ocr_pdf,
                 clock: Callable[[], float] = time.time, retention: float = RETENTION_SECONDS):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._ocr = ocr
        self._clock = clock
        self._retention = retention
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._skip: set[str] = set()  # scans whose OCR bookkeeping failed; not retried in this process

    # -- file helpers (callers hold the lock) --
    def _pdf(self, scan_id: str) -> Path:
        return self._root / f"{scan_id}.pdf"

    def _meta(self, scan_id: str) -> Path:
        return self._root / f"{scan_id}.json"

    def _read_meta(self, scan_id: str) -> dict | None:
        if not ID_RE.match(scan_id) or not self._pdf(scan_id).is_file():
            return None
        try:
            meta = json.loads(self._meta(scan_id).read_text())
            created = datetime.fromisoformat(meta["created"]).timestamp()
            return {"name": str(meta["name"]), "pages": int(meta["pages"]), "created": meta["created"],
                    "ocr": meta["ocr"], "languages": list(meta.get("languages", [])), "_ts": created}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_meta(self, scan_id: str, meta: dict) -> None:
        data = {k: meta[k] for k in ("name", "pages", "created", "ocr", "languages")}
        fd, tmp = tempfile.mkstemp(dir=self._root, suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self._meta(scan_id))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _remove(self, scan_id: str) -> None:
        self._meta(scan_id).unlink(missing_ok=True)
        self._pdf(scan_id).unlink(missing_ok=True)

    def _ids(self) -> list[str]:
        return [p.stem for p in self._root.glob("*.json") if ID_RE.match(p.stem)]

    def _entry(self, scan_id: str, meta: dict) -> dict:
        ocr = "pending" if meta["ocr"] == "running" else meta["ocr"]
        return {"id": scan_id, "name": meta["name"], "pages": meta["pages"],
                "bytes": self._pdf(scan_id).stat().st_size, "created": meta["created"], "ocr": ocr}

    def _entries(self) -> list[tuple[float, dict, dict]]:
        now = self._clock()
        found = []
        for scan_id in self._ids():
            meta = self._read_meta(scan_id)
            if meta is None:
                continue
            if now - meta["_ts"] > self._retention:
                self._remove(scan_id)
                continue
            try:
                found.append((meta["_ts"], self._entry(scan_id, meta), meta))
            except OSError:
                continue
        found.sort(key=lambda t: (t[0], t[1]["id"]))
        return found

    # -- public API --
    def add(self, pdf_path: Path, name: str, pages: int, *, skip_ocr: bool, languages: list[str]) -> str:
        with self._lock:
            scan_id = secrets.token_urlsafe(16)
            while not ID_RE.match(scan_id) or self._pdf(scan_id).exists():
                scan_id = secrets.token_urlsafe(16)
            tmp = self._root / f"{scan_id}.add.tmp"
            try:
                shutil.copyfile(pdf_path, tmp)  # copy, not move: works across filesystems
                os.replace(tmp, self._pdf(scan_id))  # the PDF first, then the JSON that makes it visible
                created = datetime.fromtimestamp(self._clock(), timezone.utc).isoformat()
                self._write_meta(scan_id, {"name": name, "pages": pages, "created": created,
                                           "ocr": "done" if skip_ocr else "pending", "languages": list(languages)})
            except BaseException:
                tmp.unlink(missing_ok=True)
                self._remove(scan_id)
                raise
            try:
                Path(pdf_path).unlink()
            except OSError:
                logger.warning("could not remove the source of a stored button scan", exc_info=True)
            self._cond.notify_all()
            return scan_id

    def list(self) -> list[dict]:
        with self._lock:
            return [e for _, e, _ in self._entries()]

    def get(self, scan_id: str) -> tuple[Path, dict] | None:
        if not isinstance(scan_id, str) or not ID_RE.match(scan_id):
            return None
        with self._lock:
            for _, entry, _ in self._entries():
                if entry["id"] == scan_id:
                    return self._pdf(scan_id), entry
            return None

    def delete(self, scan_id: str) -> bool:
        if not isinstance(scan_id, str) or not ID_RE.match(scan_id):
            return False
        with self._lock:
            if not self._meta(scan_id).exists() and not self._pdf(scan_id).exists():
                return False
            self._remove(scan_id)
            return True

    @property
    def waiting(self) -> int:
        with self._lock:
            return len(self._entries())

    def _has_pending(self) -> bool:
        return any(m["ocr"] in ("pending", "running") and e["id"] not in self._skip
                   for _, e, m in self._entries())

    def run_next(self) -> bool:
        with self._lock:
            job = next(((e["id"], m) for _, e, m in self._entries()
                        if m["ocr"] in ("pending", "running") and e["id"] not in self._skip), None)
            if job is None:
                return False
            scan_id, meta = job
            source, tmp = self._pdf(scan_id), self._root / f"{scan_id}.ocr.tmp"
            meta["ocr"] = "running"
            try:
                self._write_meta(scan_id, meta)
            except OSError:
                logger.warning("could not mark a button scan as running", exc_info=True)
                self._skip.add(scan_id)
                return True
        ok = False
        try:
            self._ocr(source, tmp, list(meta["languages"]))
            ok = tmp.is_file()
        except Exception:  # noqa: BLE001 - OcrFailed or anything else: keep the PDF as it is
            logger.warning("OCR of a button scan failed", exc_info=True)
        with self._lock:
            try:
                current = self._read_meta(scan_id)
                if current is None:  # deleted meanwhile
                    tmp.unlink(missing_ok=True)
                    return True
                if self._clock() - current["_ts"] > self._retention:  # expired meanwhile
                    tmp.unlink(missing_ok=True)
                    self._remove(scan_id)
                    return True
                if ok:
                    os.replace(tmp, source)
                current["ocr"] = "done" if ok else "failed"
                self._write_meta(scan_id, current)
            except OSError:
                logger.warning("could not finish OCR bookkeeping of a button scan", exc_info=True)
                tmp.unlink(missing_ok=True)
                self._skip.add(scan_id)
                try:
                    current = self._read_meta(scan_id)
                    if current is not None:
                        current["ocr"] = "failed"
                        self._write_meta(scan_id, current)
                except OSError:
                    logger.warning("could not mark a button scan as failed", exc_info=True)
        return True

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping = False
            self._thread = threading.Thread(target=self._work, name="button-scan-ocr", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._stopping = True
            self._cond.notify_all()
        if thread is not None:
            thread.join(timeout=10)
            if not thread.is_alive():
                with self._lock:
                    if self._thread is thread:
                        self._thread = None

    def _work(self) -> None:
        while True:
            with self._lock:
                if self._stopping:
                    return
            try:
                did = self.run_next()
            except Exception:  # noqa: BLE001 - the worker must survive
                logger.exception("button scan worker error")
                did = False
            if did:
                continue
            with self._cond:
                if self._stopping:
                    return
                if self._has_pending():
                    continue
                self._cond.wait(WORKER_WAIT_SECONDS)
