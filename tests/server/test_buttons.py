import re
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from scanix500.collection import ScanCollection
from scanix500.menubar.button_watcher import FINISH
from scanix500.menubar.runner import ScanResult
from scanix500.scanners import CANOSCAN_8800F_ID
from scanix500.server.button_scans import ButtonScanStore
from scanix500.server.buttons import ButtonCollector
from scanix500.server.config import ServerConfig
from scanix500.server.runtime import ServerScanTrigger

USB = {(0x04A9, 0x1901)}


class Rig:
    def __init__(self, tmp_path, reads, usb=USB, free=10**10, ok=True):
        self.tmp = tmp_path
        self.profiles = []
        self.now = [1_800_000_000.0]
        self.reads = list(reads)
        self.read_calls = 0
        self.ok = ok
        self.cfg = ServerConfig(port=1, name="x", state_dir=tmp_path / "state",
                                button_custom_mode="gray", button_custom_resolution=600)
        self.cfg.state_dir.mkdir()
        self.trigger = ServerScanTrigger(run=self._run)
        self.collection = ScanCollection(self.cfg.collection_path, clock=lambda: self.now[0])
        self.store = ButtonScanStore(self.cfg.button_scans_path, ocr=lambda a, b, c: None)
        self.usb, self.free = usb, free
        self.resolves = 0
        self.collector = self.make()

    def make(self, collection=None):
        return ButtonCollector(collection or self.collection, self.store, self.trigger, self.cfg,
                               read=self._read, resolve=self._resolve, usb=lambda: self.usb,
                               free_bytes=lambda p: self.free, clock=lambda: self.now[0])

    def _resolve(self, scanner):
        self.resolves += 1
        return "pixma:dev"

    def _read(self, device):
        self.read_calls += 1
        r = self.reads.pop(0) if self.reads else None
        if isinstance(r, Exception):
            raise r
        return r

    def _run(self, profile):
        self.profiles.append(profile)
        if not self.ok:
            return ScanResult(ok=False, partial=False, message="boom", output_paths=[])
        p = self.tmp / f"in{len(self.profiles)}.pdf"
        Image.new("RGB", (60, 80)).save(p, "PDF")
        return ScanResult(ok=True, partial=False, message="", output_paths=[str(p)])

    def tick(self, n=1):
        for _ in range(n):
            self.collector.poll_once()


C, B, CU = {"color_mode": "color"}, {"color_mode": "bw"}, {}


def test_buttons_4_5_6_scan_pages(tmp_path):
    r = Rig(tmp_path, [C, B, CU])
    r.tick(3)
    assert [p.color_mode for p in r.profiles] == ["color", "bw", "gray"]
    assert r.profiles[2].resolution == 600 and r.profiles[0].resolution == 300
    assert all(p.skip_ocr and p.scanner == CANOSCAN_8800F_ID and p.format == "pdf" for p in r.profiles)
    assert r.collection.page_count == 3


def test_finish_puts_one_pdf_in_the_store(tmp_path):
    r = Rig(tmp_path, [C, C, FINISH])
    r.tick(3)
    (entry,) = r.store.list()
    assert entry["pages"] == 2 and re.fullmatch(r"scan_\d{4}-\d\d-\d\d_\d{6}\.pdf", entry["name"])
    assert r.collection.page_count == 0
    assert not list(r.cfg.collection_path.glob("page-*.pdf"))
    path, _ = r.store.get(entry["id"])
    assert len(PdfReader(str(path)).pages) == 2


def test_finish_with_nothing_collected_adds_nothing(tmp_path):
    r = Rig(tmp_path, [FINISH])
    r.tick()
    assert r.store.list() == []


def test_timeout_finishes_and_a_press_resets_it(tmp_path):
    r = Rig(tmp_path, [C, None, C, None, None])
    r.tick()
    r.now[0] += 599
    r.tick()
    assert r.collection.page_count == 1
    r.tick()  # press at t=599 resets the deadline to 1199
    r.now[0] += 599
    r.tick()
    assert r.store.list() == []
    r.now[0] += 2
    r.tick()
    assert len(r.store.list()) == 1 and r.collection.page_count == 0


def test_no_usb_device_never_reads(tmp_path):
    r = Rig(tmp_path, [C], usb=set())
    r.tick(2)
    assert r.read_calls == 0 and r.resolves == 0


def test_busy_trigger_never_reads(tmp_path):
    r = Rig(tmp_path, [C])
    with r.trigger.exclusive():
        r.tick()
    assert r.read_calls == 0


def test_failed_scan_keeps_earlier_pages(tmp_path):
    r = Rig(tmp_path, [C, C, C])
    r.tick()
    r.ok = False
    r.tick()
    assert r.collection.page_count == 1
    r.ok = True
    r.tick()
    assert r.collection.page_count == 2


def test_disk_guard(tmp_path):
    r = Rig(tmp_path, [C], free=199 * 1024 * 1024)
    r.tick()
    assert r.profiles == [] and r.collection.page_count == 0


def test_read_exception_forgets_the_device(tmp_path):
    r = Rig(tmp_path, [RuntimeError("x"), None])
    with pytest.raises(RuntimeError):
        r.tick()
    r.tick()
    assert r.resolves == 2


def test_restart_continues_the_collection(tmp_path):
    r = Rig(tmp_path, [C, C])
    r.tick(2)
    r.reads = [FINISH]
    again = r.make(ScanCollection(r.cfg.collection_path, clock=lambda: r.now[0]))
    again.poll_once()
    (entry,) = r.store.list()
    assert entry["pages"] == 2


def test_finish_now_while_busy_loses_nothing(tmp_path):
    r = Rig(tmp_path, [C])
    r.tick()
    with r.trigger.exclusive():
        assert r.collector.finish_now() is None
    assert r.collection.page_count == 1 and r.store.list() == []
    assert r.collector.finish_now() is not None
    assert len(r.store.list()) == 1
