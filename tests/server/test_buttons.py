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
    def __init__(self, tmp_path, reads, usb=USB, free=10**10, ok=True, trigger=None):
        self.tmp = tmp_path
        self.profiles = []
        self.now = [1_800_000_000.0]
        self.reads = list(reads)
        self.read_calls = 0
        self.ok = ok
        self.cfg = ServerConfig(port=1, name="x", state_dir=tmp_path / "state",
                                button_custom_mode="gray", button_custom_resolution=600)
        self.cfg.state_dir.mkdir()
        self.trigger = trigger or ServerScanTrigger(run=self._run)
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


def test_failing_merge_is_retried_once_after_back_off_and_logs_once(tmp_path, monkeypatch, caplog):
    r = Rig(tmp_path, [C])
    r.tick()
    r.now[0] += 601
    calls = []
    real = r.collection.finish
    def boom(out):
        calls.append(out)
        raise OSError("disk full")
    monkeypatch.setattr(r.collection, "finish", boom)
    r.reads = [None, None, None]
    with caplog.at_level("WARNING"):
        r.tick(3)
    assert len(calls) == 1 and r.read_calls == 4 and r.collection.page_count == 1
    assert len([x for x in caplog.records if x.levelname == "WARNING"]) == 1
    monkeypatch.setattr(r.collection, "finish", real)
    r.now[0] += 301
    r.tick()
    assert len(r.store.list()) == 1 and r.collection.page_count == 0


def test_store_failure_keeps_the_merged_pdf_and_a_later_tick_delivers_it(tmp_path, monkeypatch):
    r = Rig(tmp_path, [C, FINISH])
    r.tick()
    real = r.store.add
    monkeypatch.setattr(r.store, "add", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    r.tick()
    assert list((r.cfg.state_dir / "button-finished").glob("*.pdf")) and r.store.list() == []
    monkeypatch.setattr(r.store, "add", real)
    r.now[0] += 301
    r.tick()
    assert len(r.store.list()) == 1
    assert not list((r.cfg.state_dir / "button-finished").glob("*.pdf"))


def test_sweep_delivers_a_leftover_finished_pdf_and_keeps_invalid_ones(tmp_path):
    r = Rig(tmp_path, [])
    fin = r.cfg.state_dir / "button-finished"
    fin.mkdir()
    Image.new("RGB", (60, 80)).save(fin / "scan_old.pdf", "PDF")
    (fin / "bad.pdf").write_bytes(b"nope")
    (r.cfg.state_dir / "button-tmp" / "abc").mkdir(parents=True)
    r.collector.sweep()
    assert [e["name"] for e in r.store.list()] == ["scan_old.pdf"]
    assert list((r.cfg.state_dir / "button-tmp" / "kept").glob("*bad.pdf"))
    assert not (r.cfg.state_dir / "button-tmp" / "abc").exists()


def test_scan_with_two_outputs_loses_nothing(tmp_path):
    r = Rig(tmp_path, [C])
    def run(profile):
        paths = []
        for i in range(2):
            p = Path(profile.destination) / f"o{i}.pdf"
            Image.new("RGB", (60, 80)).save(p, "PDF")
            paths.append(str(p))
        return ScanResult(ok=True, partial=False, message="", output_paths=paths)
    r.trigger = ServerScanTrigger(run=run)
    r.collector = r.make()
    r.tick()
    assert r.collection.page_count == 2
    assert not [p for p in (r.cfg.state_dir / "button-tmp").iterdir() if p.name != "kept"]


def test_unusable_output_is_kept(tmp_path):
    r = Rig(tmp_path, [C])
    def run(profile):
        p = Path(profile.destination) / "x.pdf"
        p.write_bytes(b"junk")
        return ScanResult(ok=True, partial=False, message="", output_paths=[str(p)])
    r.trigger = ServerScanTrigger(run=run)
    r.collector = r.make()
    r.tick()
    assert r.collection.page_count == 0
    assert list((r.cfg.state_dir / "button-tmp" / "kept").glob("*x.pdf"))


def test_stop_joins_the_thread(tmp_path):
    r = Rig(tmp_path, [])
    r.collector._interval = 0.01
    r.collector.start()
    thread = r.collector._thread
    r.collector.stop()
    assert not thread.is_alive()


class _NoLock:
    busy = False
    def exclusive(self):
        from scanix500.menubar.bridge import ScanBusyError
        raise ScanBusyError()


def test_reads_never_happen_without_the_trigger_lock(tmp_path):
    r = Rig(tmp_path, [C], trigger=_NoLock())
    r.collector.poll_once()
    assert r.read_calls == 0 and r.resolves == 0


def test_reads_and_resolve_happen_inside_exclusive(tmp_path):
    r = Rig(tmp_path, [None])
    seen = []
    orig_read, orig_resolve = r._read, r._resolve
    r._read = lambda d: (seen.append(r.trigger.busy), orig_read(d))[1]
    r._resolve = lambda s: (seen.append(r.trigger.busy), orig_resolve(s))[1]
    r.collector = r.make()
    r.tick()
    assert seen == [True, True]


def test_device_forgotten_when_scanner_unplugged_and_resolve_backs_off(tmp_path):
    r = Rig(tmp_path, [None])
    r._resolve = lambda s: (setattr(r, "resolves", r.resolves + 1), None)[1]
    r.collector = r.make()
    r.tick(5)
    assert r.resolves == 1


def test_expired_at_restart_finishes_on_the_first_tick(tmp_path):
    r = Rig(tmp_path, [C])
    r.tick()
    r.now[0] += 601
    again = r.make(ScanCollection(r.cfg.collection_path, clock=lambda: r.now[0]))
    again.poll_once()
    assert len(r.store.list()) == 1
