import json
import shutil
import threading
from datetime import datetime, timezone

import pytest
from PIL import Image

from scanix500.pdf_builder import OcrFailed
from scanix500.server.button_scans import ID_RE, ButtonScanStore


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_pdf(path, width=60):
    Image.new("RGB", (width, 80)).save(path, "PDF")
    return path


def copy_ocr(calls=None):
    def ocr(src, dst, languages):
        if calls is not None:
            calls.append((src, dst, list(languages)))
        shutil.copyfile(src, dst)
        with open(dst, "ab") as f:
            f.write(b"\n% ocr\n")
    return ocr


def raising(exc):
    def ocr(src, dst, languages):
        raise exc
    return ocr


@pytest.fixture
def root(tmp_path):
    return tmp_path / "store"


def add(store, tmp_path, name="a.pdf", width=60, skip_ocr=False, languages=("deu", "eng")):
    return store.add(make_pdf(tmp_path / f"in-{name}", width), name, 1,
                     skip_ocr=skip_ocr, languages=list(languages))


def test_add_lists_pending(root, tmp_path):
    store = ButtonScanStore(root, ocr=copy_ocr(), clock=Clock())
    src = make_pdf(tmp_path / "in.pdf")
    size = src.stat().st_size
    sid = store.add(src, "Scan.pdf", 1, skip_ocr=False, languages=["eng"])
    assert ID_RE.match(sid)
    assert not src.exists()
    (entry,) = store.list()
    assert entry["id"] == sid and entry["name"] == "Scan.pdf" and entry["pages"] == 1
    assert entry["ocr"] == "pending" and entry["bytes"] == size
    assert store.waiting == 1


def test_skip_ocr_is_done(root, tmp_path):
    store = ButtonScanStore(root, ocr=copy_ocr(), clock=Clock())
    add(store, tmp_path, skip_ocr=True)
    assert store.list()[0]["ocr"] == "done"
    assert store.run_next() is False


def test_run_next_ocrs(root, tmp_path):
    calls = []
    store = ButtonScanStore(root, ocr=copy_ocr(calls), clock=Clock())
    sid = add(store, tmp_path, languages=("deu", "eng"))
    before = (root / f"{sid}.pdf").read_bytes()
    assert store.run_next() is True
    assert len(calls) == 1 and calls[0][2] == ["deu", "eng"]
    after = (root / f"{sid}.pdf").read_bytes()
    assert after == before + b"\n% ocr\n"
    entry = store.list()[0]
    assert entry["ocr"] == "done" and entry["bytes"] == len(after)
    assert store.run_next() is False
    assert not list(root.glob("*.tmp"))


def test_ocr_failed_keeps_pdf(root, tmp_path):
    store = ButtonScanStore(root, ocr=raising(OcrFailed("no")), clock=Clock())
    sid = add(store, tmp_path)
    before = (root / f"{sid}.pdf").read_bytes()
    assert store.run_next() is True
    assert (root / f"{sid}.pdf").read_bytes() == before
    assert store.list()[0]["ocr"] == "failed"
    assert store.run_next() is False


def test_other_exception_is_failed(root, tmp_path):
    store = ButtonScanStore(root, ocr=raising(RuntimeError("boom")), clock=Clock())
    add(store, tmp_path)
    assert store.run_next() is True
    assert store.list()[0]["ocr"] == "failed"


def test_get(root, tmp_path):
    store = ButtonScanStore(root, ocr=copy_ocr(), clock=Clock())
    sid = add(store, tmp_path)
    path, entry = store.get(sid)
    assert path == root / f"{sid}.pdf" and entry["id"] == sid
    assert store.get("x" * 22) is None
    assert store.get("../x") is None
    assert store.get("short") is None
    assert store.get("../" + "a" * 30) is None


def test_delete(root, tmp_path):
    store = ButtonScanStore(root, ocr=copy_ocr(), clock=Clock())
    sid = add(store, tmp_path)
    assert store.delete(sid) is True
    assert not (root / f"{sid}.pdf").exists() and not (root / f"{sid}.json").exists()
    assert store.delete(sid) is False
    assert store.delete("../x") is False
    assert store.list() == []


def test_oldest_first_and_created_iso(root, tmp_path):
    clock = Clock(1_000_000.0)
    store = ButtonScanStore(root, ocr=copy_ocr(), clock=clock)
    first = add(store, tmp_path, "one.pdf")
    clock.t += 50
    second = add(store, tmp_path, "two.pdf")
    entries = store.list()
    assert [e["id"] for e in entries] == [first, second]
    assert entries[0]["created"] == datetime.fromtimestamp(1_000_000.0, timezone.utc).isoformat()


def test_expiry(root, tmp_path):
    clock = Clock()
    store = ButtonScanStore(root, ocr=copy_ocr(), clock=clock, retention=100)
    sid = add(store, tmp_path)
    clock.t += 99
    assert len(store.list()) == 1
    clock.t += 2
    assert store.list() == []
    assert not (root / f"{sid}.pdf").exists() and not (root / f"{sid}.json").exists()


def test_restart_and_running_state(root, tmp_path):
    store = ButtonScanStore(root, ocr=copy_ocr(), clock=Clock())
    sid = add(store, tmp_path)
    meta = root / f"{sid}.json"
    data = json.loads(meta.read_text())
    data["ocr"] = "running"
    meta.write_text(json.dumps(data))
    again = ButtonScanStore(root, ocr=copy_ocr(), clock=Clock())
    assert [e["id"] for e in again.list()] == [sid]
    assert again.waiting == 1
    assert again.run_next() is True
    assert again.list()[0]["ocr"] == "done"


def test_incomplete_files_ignored(root, tmp_path):
    store = ButtonScanStore(root, ocr=copy_ocr(), clock=Clock())
    sid = add(store, tmp_path)
    (root / ("a" * 22 + ".json")).write_text(json.dumps(
        {"name": "x", "pages": 1, "created": "2026-01-01T00:00:00+00:00", "ocr": "done", "languages": []}))
    make_pdf(root / ("b" * 22 + ".pdf"))
    assert [e["id"] for e in store.list()] == [sid]
    assert store.get("a" * 22) is None and store.get("b" * 22) is None


def test_start_stop_worker(root, tmp_path):
    done = threading.Event()

    def ocr(src, dst, languages):
        shutil.copyfile(src, dst)
        done.set()

    store = ButtonScanStore(root, ocr=ocr, clock=Clock())
    store.start()
    try:
        sid = add(store, tmp_path)
        assert done.wait(5)
        for _ in range(100):
            if store.list()[0]["ocr"] == "done":
                break
            threading.Event().wait(0.05)
        assert store.list()[0]["ocr"] == "done" and store.list()[0]["id"] == sid
    finally:
        store.stop()
