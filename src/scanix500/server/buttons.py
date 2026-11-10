"""The CanoScan 8800F's PDF buttons on the Pi: pages are scanned into a
ScanCollection; FINISH (or a quiet period) turns them into one PDF on the
waiting list."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from scanix500.collection import CollectionSettings, ScanCollection
from scanix500.i18n import t
from scanix500.menubar.bridge import ScanBusyError
from scanix500.menubar.button_watcher import FINISH, pixma_button_overrides, resolve_device_name
from scanix500.menubar.profiles import Profile
from scanix500.scanners import CANOSCAN_8800F_ID, usb_ids
from scanix500.server.button_scans import ButtonScanStore
from scanix500.server.config import ServerConfig
from scanix500.server.runtime import ServerScanTrigger

logger = logging.getLogger(__name__)

MIN_FREE_BYTES = 200 * 1024 * 1024
_USB_ID = (0x04A9, 0x1901)


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


class ButtonCollector:
    def __init__(self, collection: ScanCollection, store: ButtonScanStore, trigger: ServerScanTrigger,
                 cfg: ServerConfig, *, read: Callable[[str], dict | str | None] = pixma_button_overrides,
                 resolve: Callable[[str], str | None] = resolve_device_name,
                 usb: Callable[[], set] = usb_ids, free_bytes: Callable[[Path], int] = _free_bytes,
                 clock: Callable[[], float] = time.time, interval: float = 1.5):
        self._collection, self._store, self._trigger, self._cfg = collection, store, trigger, cfg
        self._read, self._resolve, self._usb, self._free_bytes = read, resolve, usb, free_bytes
        self._clock, self._interval = clock, interval
        self._lock = threading.Lock()  # ScanCollection is not thread-safe
        self._device: str | None = None
        self._stop = threading.Event()
        self._tmp = cfg.state_dir / "button-tmp"

    # -- one tick ---------------------------------------------------------
    def poll_once(self) -> None:
        timeout = self._cfg.collect_timeout_minutes * 60
        with self._lock:
            expired = self._collection.expired(timeout)
        if expired:
            self.finish_now()
        if self._trigger.busy or _USB_ID not in self._usb():
            return
        if self._device is None:
            self._device = self._resolve(CANOSCAN_8800F_ID)
            if self._device is None:
                return
        try:
            hit = self._read(self._device)
        except Exception:
            self._device = None
            raise
        if hit == FINISH:
            self.finish_now()
        elif isinstance(hit, dict):
            self.scan_page(hit)

    def scan_page(self, overrides: dict) -> None:
        self._tmp.mkdir(parents=True, exist_ok=True)
        if self._free_bytes(self._cfg.state_dir) < MIN_FREE_BYTES:
            logger.warning("%s", t("Button scan refused: less than 200 MB free."))
            return
        mode = overrides.get("color_mode")
        if mode is None:  # CUSTOM
            profile = Profile(name="Button", destination=str(self._tmp), skip_ocr=True, scanner=CANOSCAN_8800F_ID,
                              color_mode=self._cfg.button_custom_mode,
                              resolution=self._cfg.button_custom_resolution, format="pdf")
        else:
            profile = Profile(name="Button", destination=str(self._tmp), skip_ocr=True, scanner=CANOSCAN_8800F_ID,
                              color_mode=mode, resolution=300, format="pdf")
        try:
            result = self._trigger.trigger(profile)
        except ScanBusyError:
            return
        if not result.ok or len(result.output_paths) != 1:
            logger.warning("Button scan failed: %s", result.message)
            return
        settings = CollectionSettings(skip_ocr=False, ocr_languages=["deu", "eng"], scanner=CANOSCAN_8800F_ID)
        with self._lock:
            self._collection.add_page(Path(result.output_paths[0]), settings)

    def finish_now(self) -> Path | None:
        try:
            with self._trigger.exclusive():
                with self._lock:
                    pages = self._collection.page_count
                    if pages == 0:
                        return None
                    settings = self._collection.settings
                    self._tmp.mkdir(parents=True, exist_ok=True)
                    name = f"scan_{datetime.fromtimestamp(self._clock()).strftime('%Y-%m-%d_%H%M%S')}.pdf"
                    path = self._collection.finish(self._tmp / name)
                    self._store.add(path, name, pages,
                                    skip_ocr=settings.skip_ocr if settings else False,
                                    languages=settings.ocr_languages if settings else ["deu", "eng"])
                    return path
        except ScanBusyError:
            return None

    def status_lines(self) -> list[str]:
        with self._lock:
            pages = self._collection.page_count
            last = self._collection.last_activity
        if pages:
            left = max(0, int((last or self._clock()) + self._cfg.collect_timeout_minutes * 60 - self._clock()))
            return [t("Button scans: collecting {count} pages (closes in {minutes} min)",
                      count=pages, minutes=(left + 59) // 60)]
        return [t("Button scans: nothing collected")]

    # -- thread -----------------------------------------------------------
    def start(self) -> None:
        def loop():
            while not self._stop.wait(self._interval):
                try:
                    self.poll_once()
                except Exception:  # noqa: BLE001 - never stop watching over one hiccup
                    logger.exception("button poll failed")
                    self._device = None
        threading.Thread(target=loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
