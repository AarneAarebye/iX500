"""Collecting 8800F button pages on the Mac into one PDF.

No rumps/AppKit imports here: app.py wires this to the menu bar.
"""
import json
import logging
import os
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from scanix500.collection import CollectionSettings, ScanCollection
from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import run_scan
from scanix500.pdf_builder import OCR_LANGUAGE, OcrFailed, ocr_pdf

logger = logging.getLogger(__name__)

_LANGUAGES = OCR_LANGUAGE.split("+")
DEFAULT_TIMEOUT_MINUTES = 10


@dataclass(frozen=True)
class PageOutcome:
    ok: bool
    pages: int
    first_page: bool
    message: str


@dataclass(frozen=True)
class FinishOutcome:
    path: Path | None
    pages: int
    searchable: bool
    message: str


def _app_support() -> Path:
    return Path.home() / "Library" / "Application Support" / "scanix500"


def default_collection_config_path() -> Path:
    return _app_support() / "collection.json"


def default_collection_folder() -> Path:
    return _app_support() / "collection"


def load_collect_timeout(path: Path) -> int:
    """Minutes of inactivity before a collection is finished; never raises."""
    try:
        value = json.loads(Path(path).read_text())["collect_timeout_minutes"]
    except Exception:  # noqa: BLE001
        return DEFAULT_TIMEOUT_MINUTES
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return DEFAULT_TIMEOUT_MINUTES
    return value


def _page_count(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            if f.read(5) != b"%PDF-":
                return 0
        return len(PdfReader(str(path)).pages)
    except Exception:  # noqa: BLE001
        return 0


class MacCollector:
    def __init__(self, folder: Path, scratch: Path, *, timeout_seconds: float = 600.0,
                 run=run_scan, ocr=ocr_pdf, clock=time.time):
        self._scratch = Path(scratch)
        self._timeout = timeout_seconds
        self._run = run
        self._ocr = ocr
        self._clock = clock
        self._lock = threading.Lock()
        self._collection = ScanCollection(Path(folder), clock=clock)

    @property
    def page_count(self) -> int:
        return self._collection.page_count

    @property
    def is_open(self) -> bool:
        return self._collection.is_open

    def expired(self) -> bool:
        return self._collection.expired(self._timeout)

    def scan_page(self, profile: Profile, overrides: dict) -> PageOutcome:
        with self._lock:
            was_open = self._collection.is_open
            folder = self._scratch / secrets.token_hex(8)
            try:
                folder.mkdir(parents=True, exist_ok=True)
                scan_profile = replace(profile, **overrides, destination=str(folder),
                                       skip_ocr=True, format="pdf")
                try:
                    result = self._run(scan_profile)
                except Exception as e:  # noqa: BLE001
                    logger.exception("page scan raised")
                    return PageOutcome(False, self._collection.page_count, False, str(e))
                if not result.ok:
                    self._cleanup(folder)
                    return PageOutcome(False, self._collection.page_count, False, result.message)
                settings = CollectionSettings(
                    destination=profile.destination, skip_ocr=profile.skip_ocr,
                    ocr_languages=list(_LANGUAGES), scanner=scan_profile.scanner)
                added = self._take_pages(folder, result.output_paths, settings)
            except Exception as e:  # noqa: BLE001
                logger.exception("page scan failed")
                return PageOutcome(False, self._collection.page_count, False, str(e))
            finally:
                self._cleanup(folder)
            if not added:
                return PageOutcome(False, self._collection.page_count, False,
                                   "The scan produced no usable page.")
            return PageOutcome(True, self._collection.page_count, not was_open, "")

    def finish(self) -> FinishOutcome:
        with self._lock:
            settings = self._collection.settings
            pages = self._collection.page_count
            if settings is None or pages == 0:
                return FinishOutcome(None, 0, False, "No pages collected.")
            stamp = datetime.fromtimestamp(self._clock()).strftime("%Y-%m-%d_%H%M%S")
            destination = Path(settings.destination)
            name, n = f"scan_{stamp}.pdf", 2
            while (destination / name).exists():
                name, n = f"scan_{stamp}_{n}.pdf", n + 1
            out = destination / name
            try:
                self._collection.finish(out)
            except Exception as e:  # noqa: BLE001
                logger.warning("could not merge the collected pages: %s", e)
                return FinishOutcome(None, pages, False, str(e))
            if settings.skip_ocr:
                return FinishOutcome(out, pages, False, "")
            tmp = out.with_suffix(".ocr.tmp")
            try:
                self._ocr(out, tmp, list(settings.ocr_languages))
                os.replace(tmp, out)
            except OcrFailed as e:
                tmp.unlink(missing_ok=True)
                return FinishOutcome(out, pages, False, str(e))
            except Exception as e:  # noqa: BLE001
                tmp.unlink(missing_ok=True)
                return FinishOutcome(out, pages, False, str(e))
            return FinishOutcome(out, pages, True, "")

    def discard(self) -> None:
        with self._lock:
            self._collection.discard()

    # -- internals ---------------------------------------------------------
    def _take_pages(self, folder: Path, paths: list[str], settings: CollectionSettings) -> int:
        produced = {Path(p) for p in paths if Path(p).is_file()}
        if folder.is_dir():
            produced |= {p for p in folder.glob("*.pdf") if p.is_file()}
        added = 0
        for path in sorted(produced):
            ok = False
            if _page_count(path) == 1:
                try:
                    self._collection.add_page(path, settings)
                    ok = True
                    added += 1
                except Exception:  # noqa: BLE001
                    logger.exception("could not add a scanned page")
            if not ok and path.exists():
                self._keep(path)
        return added

    def _keep(self, path: Path) -> None:
        try:
            kept = self._scratch / "kept"
            kept.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), kept / f"{secrets.token_hex(4)}-{path.name}")
        except OSError:
            logger.exception("could not keep %s", path)

    def _cleanup(self, folder: Path) -> None:
        if folder.is_dir():
            for leftover in list(folder.iterdir()):
                if leftover.is_file():
                    self._keep(leftover)
        try:
            folder.rmdir()
        except OSError:
            pass
