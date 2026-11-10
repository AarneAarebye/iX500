import json

import pytest
from PIL import Image
from pypdf import PdfReader

from scanix500 import collection as collection_module
from scanix500.collection import CollectionSettings, ScanCollection

SETTINGS = CollectionSettings(skip_ocr=True, ocr_languages=["fra"], scanner="sane:ix500")
DEFAULTS = CollectionSettings(skip_ocr=False, ocr_languages=["deu", "eng"], scanner="sane:canoscan-8800f")


def make_pdf(path, width=60):
    Image.new("RGB", (width, 80)).save(path, "PDF")
    return path


class Clock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


def widths(path):
    return [round(float(p.mediabox.width)) for p in PdfReader(str(path)).pages]


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    return d


def fill(col, src, n=3):
    for i in range(n):
        col.add_page(make_pdf(src / f"s{i}.pdf", 60 + i), SETTINGS)


def test_empty(tmp_path):
    col = ScanCollection(tmp_path / "c", clock=Clock())
    assert col.page_count == 0
    assert not col.is_open
    assert col.settings is None
    assert col.last_activity is None
    assert not col.expired(600)


def test_add_page_moves_files(tmp_path, src):
    col = ScanCollection(tmp_path / "c")
    paths = [make_pdf(src / f"s{i}.pdf") for i in range(3)]
    counts = [col.add_page(p, SETTINGS) for p in paths]
    assert counts == [1, 2, 3]
    assert col.page_count == 3
    assert col.is_open
    assert not any(p.exists() for p in paths)


def test_finish_merges_in_order(tmp_path, src):
    folder = tmp_path / "c"
    col = ScanCollection(folder)
    fill(col, src)
    out = tmp_path / "out.pdf"
    assert col.finish(out) == out
    assert widths(out) == [60, 61, 62]
    assert not list(folder.glob("page-*.pdf"))
    assert not (folder / "meta.json").exists()
    assert col.page_count == 0
    assert not col.is_open


def test_finish_empty_raises(tmp_path):
    col = ScanCollection(tmp_path / "c")
    out = tmp_path / "out.pdf"
    with pytest.raises(ValueError):
        col.finish(out)
    assert not out.exists()


def test_finish_failure_keeps_everything(tmp_path, src):
    folder = tmp_path / "c"
    col = ScanCollection(folder)
    fill(col, src)
    with pytest.raises(OSError):
        col.finish(tmp_path / "missing" / "out.pdf")
    assert col.page_count == 3
    assert (folder / "meta.json").exists()
    assert len(list(folder.glob("page-*.pdf"))) == 3


def test_finish_merge_error_keeps_everything(tmp_path, src, monkeypatch):
    folder = tmp_path / "c"
    col = ScanCollection(folder)
    fill(col, src)

    def boom(*a, **k):
        raise RuntimeError("merge failed")

    monkeypatch.setattr(collection_module, "_merge_pdfs", boom)
    out = tmp_path / "out.pdf"
    with pytest.raises(RuntimeError):
        col.finish(out)
    assert col.page_count == 3
    assert (folder / "meta.json").exists()
    assert not out.exists()
    assert not out.with_suffix(".tmp").exists()


def test_expired(tmp_path, src):
    clock = Clock(0)
    col = ScanCollection(tmp_path / "c", clock=clock)
    col.add_page(make_pdf(src / "a.pdf"), SETTINGS)
    clock.now = 599
    assert not col.expired(600)
    clock.now = 600
    assert col.expired(600)
    clock.now = 500
    col.add_page(make_pdf(src / "b.pdf"), SETTINGS)
    clock.now = 1099
    assert not col.expired(600)
    clock.now = 1100
    assert col.expired(600)
    assert col.last_activity == 500


def test_settings_from_first_page(tmp_path, src):
    col = ScanCollection(tmp_path / "c")
    col.add_page(make_pdf(src / "a.pdf"), SETTINGS)
    other = CollectionSettings(skip_ocr=False, ocr_languages=["deu"], scanner="sane:canoscan-8800f")
    col.add_page(make_pdf(src / "b.pdf"), other)
    assert col.settings == SETTINGS


def test_recover_after_restart(tmp_path, src):
    folder = tmp_path / "c"
    clock = Clock(42)
    col = ScanCollection(folder, clock=clock)
    fill(col, src, 2)
    again = ScanCollection(folder, clock=Clock(1000))
    assert again.page_count == 2
    assert again.settings == SETTINGS
    assert again.last_activity == 42


def test_damaged_page(tmp_path, src):
    folder = tmp_path / "c"
    col = ScanCollection(folder)
    fill(col, src, 3)
    (folder / "page-002.pdf").write_bytes(b"nope")
    again = ScanCollection(folder)
    assert again.page_count == 2
    assert [p.name for p in again.damaged()] == ["page-002.pdf"]
    assert again.damaged()[0].parent == folder / "damaged"
    assert not (folder / "page-002.pdf").exists()
    assert again.add_page(make_pdf(src / "n.pdf", 70), SETTINGS) == 3
    out = tmp_path / "out.pdf"
    again.finish(out)
    assert widths(out) == [60, 62, 70]


def test_damaged_page_same_name_twice(tmp_path, src):
    folder = tmp_path / "c"
    col = ScanCollection(folder)
    fill(col, src, 2)
    (folder / "page-002.pdf").write_bytes(b"nope")
    ScanCollection(folder)
    (folder / "page-002.pdf").write_bytes(b"nope again")
    again = ScanCollection(folder)
    assert len(again.damaged()) == 2


def test_damaged_meta(tmp_path, src):
    folder = tmp_path / "c"
    col = ScanCollection(folder)
    fill(col, src, 2)
    (folder / "meta.json").write_text("{garbage")
    again = ScanCollection(folder)
    assert again.page_count == 2
    assert again.settings == DEFAULTS
    assert [p.name for p in again.damaged()] == ["meta.json"]
    assert again.last_activity is not None
    assert json.loads((folder / "meta.json").read_text())["settings"]["scanner"] == "sane:canoscan-8800f"


def test_discard(tmp_path, src):
    folder = tmp_path / "c"
    col = ScanCollection(folder)
    fill(col, src)
    col.discard()
    assert col.page_count == 0
    assert not list(folder.glob("page-*.pdf"))
    assert not (folder / "meta.json").exists()
    assert col.settings is None
