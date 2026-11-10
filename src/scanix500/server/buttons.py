"""The CanoScan 8800F's PDF buttons on the Pi: pages are scanned into a
ScanCollection; FINISH (or a quiet period) turns them into one PDF on the
waiting list."""

from __future__ import annotations

import logging
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from scanix500.collection import CollectionSettings, ScanCollection
from scanix500.i18n import t
from scanix500.menubar.bridge import ScanBusyError
from scanix500.menubar.button_watcher import FINISH, pixma_button_overrides, resolve_device_name
from scanix500.menubar.profiles import Profile
from scanix500.scanners import CANOSCAN_8800F_ID, get_scanner, usb_ids
from scanix500.server.button_events import ButtonEvents
from scanix500.server.button_scans import ButtonScanStore
from scanix500.server.config import ServerConfig
from scanix500.server.runtime import ServerScanTrigger

logger = logging.getLogger(__name__)

MIN_FREE_BYTES = 200 * 1024 * 1024
RETRY_AFTER_SECONDS = 300  # after a failed merge or delivery
RESOLVE_RETRY_TICKS = 4  # ~6 s: a scanner still initialising must not swallow presses for long
RESOLVE_FAIL_LOG_EVERY = 20  # after the first failure of an episode, log every 20th failed retry
SETTLE_TICKS = 3  # after a (re)plug: the Pi can freeze if SANE touches a scanner just plugged in
JOIN_SECONDS = 10
_DEFAULT_LANGUAGES = ["deu", "eng"]


def _sane_exit() -> None:
    """Shuts SANE down so the next use starts from scratch. A long-running
    process that keeps SANE initialised across a power-cycle of the scanner
    keeps reading it through a stale USB handle ("Invalid argument") until the
    service is restarted."""
    try:
        import sane

        sane.exit()
    except Exception:  # noqa: BLE001 - nothing to clean up if SANE never started
        pass


def _read_fresh(device: str) -> dict | str | None:
    try:
        return pixma_button_overrides(device)
    finally:
        _sane_exit()


def _resolve_fresh(scanner_id: str) -> str | None:
    try:
        return resolve_device_name(scanner_id)
    finally:
        _sane_exit()


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _page_count(path: Path) -> int:
    """Pages of a valid PDF, else 0."""
    try:
        with open(path, "rb") as f:
            if f.read(5) != b"%PDF-":
                return 0
        return len(PdfReader(str(path)).pages)
    except Exception:  # noqa: BLE001
        return 0


def _button_name(overrides: dict) -> str:
    return {"color": "color", "bw": "bw"}.get(overrides.get("color_mode"), "custom")


class ButtonCollector:
    def __init__(self, collection: ScanCollection, store: ButtonScanStore, trigger: ServerScanTrigger,
                 cfg: ServerConfig, *, read: Callable[[str], dict | str | None] = _read_fresh,
                 resolve: Callable[[str], str | None] = _resolve_fresh,
                 usb: Callable[[], set] = usb_ids, free_bytes: Callable[[Path], int] = _free_bytes,
                 clock: Callable[[], float] = time.time, interval: float = 1.5,
                 events: ButtonEvents | None = None):
        self._collection, self._store, self._trigger, self._cfg = collection, store, trigger, cfg
        self._read, self._resolve, self._usb, self._free_bytes = read, resolve, usb, free_bytes
        self._clock, self._interval = clock, interval
        self._events = events
        self._lock = threading.Lock()  # ScanCollection is not thread-safe
        self._device: str | None = None
        self._resolve_wait = 0
        self._settle_left = 0
        self._usb_present = False
        self._resolve_failures = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tmp = cfg.state_dir / "button-tmp"
        self._kept = self._tmp / "kept"
        self._finished = cfg.state_dir / "button-finished"
        self._retry_at = 0.0
        self._failing = False
        self._usb_id = get_scanner(CANOSCAN_8800F_ID).usb_id

    # -- one tick ---------------------------------------------------------
    def poll_once(self) -> None:
        if self._clock() >= self._retry_at:
            self._deliver_finished()
            timeout = self._cfg.collect_timeout_minutes * 60
            with self._lock:
                expired = self._collection.expired(timeout)
            if expired:
                self.finish_now()
        hit = self._read_buttons()
        if hit is not None and self._events is not None:
            self._events.record("finish" if hit == FINISH else _button_name(hit))
        if hit == FINISH:
            self.finish_now(force=True)
        elif isinstance(hit, dict):
            self.scan_page(hit)

    def _read_buttons(self) -> dict | str | None:
        """Resolve and read inside the trigger's lock, so the scanner is never
        open here while a client's scan wants it."""
        if self._usb_id not in self._usb():
            if self._usb_present:
                self._usb_present = False
                logger.info("CanoScan 8800F disappeared from USB")
            self._device = None
            self._resolve_failures = 0
            self._settle_left = SETTLE_TICKS
            return None
        if not self._usb_present:
            self._usb_present = True
            logger.info("CanoScan 8800F appeared on USB (settling for %d ticks)", SETTLE_TICKS)
        if self._settle_left > 0:
            self._settle_left -= 1
            return None
        try:
            with self._trigger.exclusive():
                if self._device is None:
                    if self._resolve_wait > 0:
                        self._resolve_wait -= 1
                        return None
                    self._device = self._resolve(CANOSCAN_8800F_ID)
                    if self._device is None:
                        self._resolve_wait = RESOLVE_RETRY_TICKS
                        if self._resolve_failures % RESOLVE_FAIL_LOG_EVERY == 0:
                            logger.warning("CanoScan 8800F is on USB but SANE does not list it yet "
                                           "(failed lookups: %d); trying again in a few seconds",
                                           self._resolve_failures + 1)
                        self._resolve_failures += 1
                        return None
                    logger.info("CanoScan 8800F resolved: %s", self._device)
                    self._resolve_failures = 0
                try:
                    return self._read(self._device)
                except Exception:
                    self._device = None
                    raise
        except ScanBusyError:
            return None

    # -- pages ------------------------------------------------------------
    def scan_page(self, overrides: dict) -> None:
        self._tmp.mkdir(parents=True, exist_ok=True)
        if self._free_bytes(self._cfg.state_dir) < MIN_FREE_BYTES:
            logger.warning("%s", t("Button scan refused: less than 200 MB free."))
            return
        if "color_mode" in overrides:
            mode, resolution = overrides["color_mode"], 300
        else:  # CUSTOM
            mode, resolution = self._cfg.button_custom_mode, self._cfg.button_custom_resolution
        folder = self._tmp / secrets.token_hex(8)
        folder.mkdir()
        profile = Profile(name="Button", destination=str(folder), skip_ocr=True, scanner=CANOSCAN_8800F_ID,
                          color_mode=mode, resolution=resolution, format="pdf")
        try:
            result = self._trigger.trigger(profile)
        except ScanBusyError:
            self._cleanup(folder)
            return
        except BaseException:
            self._cleanup(folder)
            raise
        paths = [str(p) for p in result.output_paths]
        if result.ok and not result.partial and len(paths) != 1:
            logger.warning("Button scan returned %d files instead of 1: %s", len(paths), paths)
        elif not result.ok:
            logger.warning("Button scan failed: %s", result.message)
        self._take_pages(folder, paths)

    def _take_pages(self, folder: Path, paths: list[str]) -> None:
        """Every PDF the scan produced becomes a page, or is kept; none is lost."""
        produced = {Path(p) for p in paths if Path(p).is_file()}
        produced |= {p for p in folder.glob("*.pdf") if p.is_file()}
        settings = CollectionSettings(skip_ocr=False, ocr_languages=list(_DEFAULT_LANGUAGES), scanner=CANOSCAN_8800F_ID)
        for path in sorted(produced):
            added = False
            if _page_count(path) == 1:
                try:
                    with self._lock:
                        self._collection.add_page(path, settings)
                    added = True
                    self._retry_at = 0.0  # new state: no waiting for an old failure
                except Exception:  # noqa: BLE001
                    logger.exception("could not add a scanned page")
            if not added and path.exists():
                self._keep(path)
        self._cleanup(folder)

    def _keep(self, path: Path) -> None:
        try:
            self._kept.mkdir(parents=True, exist_ok=True)
            target = self._kept / f"{secrets.token_hex(4)}-{path.name}"
            shutil.move(str(path), target)
            logger.warning("Kept a scanned file that is not a usable page: %s", target)
        except OSError:
            logger.exception("could not keep %s", path)

    def _cleanup(self, folder: Path) -> None:
        for leftover in list(folder.iterdir()) if folder.is_dir() else []:
            if leftover.is_file():
                self._keep(leftover)
        try:
            folder.rmdir()
        except OSError:
            pass

    # -- finishing --------------------------------------------------------
    def _fail(self, what: str, exc: Exception) -> None:
        self._retry_at = self._clock() + RETRY_AFTER_SECONDS
        if not self._failing:  # one line per failure episode
            self._failing = True
            logger.warning("%s: %s (the pages are kept; trying again in %d min)", what, exc,
                           RETRY_AFTER_SECONDS // 60)

    def finish_now(self, force: bool = False) -> Path | None:
        """Merge the open collection into button-finished/ and hand it to the
        waiting list. None when empty, busy, backing off or failing."""
        if not force and self._clock() < self._retry_at:
            return None
        try:
            with self._trigger.exclusive():
                with self._lock:
                    if self._collection.page_count == 0:
                        return None
                    settings = self._collection.settings
                    self._finished.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.fromtimestamp(self._clock()).strftime("%Y-%m-%d_%H%M%S")
                    name, n = f"scan_{stamp}.pdf", 2
                    while (self._finished / name).exists():
                        name, n = f"scan_{stamp}_{n}.pdf", n + 1
                    try:
                        path = self._collection.finish(self._finished / name)
                    except Exception as e:  # noqa: BLE001
                        self._fail("Could not merge the collected pages", e)
                        return None
        except ScanBusyError:
            return None
        self._deliver_finished(settings.skip_ocr if settings else False,
                               settings.ocr_languages if settings else None)
        return path

    def _deliver_finished(self, skip_ocr: bool = False, languages: list[str] | None = None) -> None:
        if not self._finished.is_dir():
            return
        for path in sorted(self._finished.glob("*.pdf")):
            pages = _page_count(path)
            if pages == 0:
                self._keep(path)
                continue
            try:
                with self._lock:
                    self._store.add(path, path.name, pages, skip_ocr=skip_ocr,
                                    languages=list(languages or _DEFAULT_LANGUAGES))
                self._failing = False
            except Exception as e:  # noqa: BLE001
                self._fail("Could not add a finished scan to the waiting list", e)
                return

    def sweep(self) -> None:
        """At start: deliver what a crash left, drop empty scratch folders."""
        self._deliver_finished()
        if self._tmp.is_dir():
            for child in self._tmp.iterdir():
                if child.is_dir() and child != self._kept:
                    self._cleanup(child)

    # -- thread -----------------------------------------------------------
    def start(self) -> None:
        self.sweep()

        def loop():
            while not self._stop.wait(self._interval):
                try:
                    self.poll_once()
                except Exception:  # noqa: BLE001 - never stop watching over one hiccup
                    logger.exception("button poll failed")
                    self._device = None
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(JOIN_SECONDS)
