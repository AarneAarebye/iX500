import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from scanix500.menubar.collecting import MacCollector, load_collect_timeout
from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult
from scanix500.pdf_builder import OcrFailed


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_pdf(path: Path, width=40, pages=1):
    imgs = [Image.new("RGB", (width, 40), "white") for _ in range(pages)]
    imgs[0].save(path, "PDF", save_all=True, append_images=imgs[1:])


def widths(path: Path):
    return [round(float(p.mediabox.width)) for p in PdfReader(str(path)).pages]


class FakeRun:
    def __init__(self):
        self.profiles = []
        self.mode = "ok"

    def __call__(self, profile):
        self.profiles.append(profile)
        dest = Path(profile.destination)
        if self.mode == "fail":
            return ScanResult(ok=False, partial=False, message="Cover open", output_paths=[])
        if self.mode == "nofile":
            return ScanResult(ok=True, partial=False, message="", output_paths=[])
        if self.mode == "notpdf":
            p = dest / "x.pdf"
            p.write_text("hello")
            return ScanResult(ok=True, partial=False, message="", output_paths=[str(p)])
        p = dest / f"s{len(self.profiles)}.pdf"
        if self.mode == "multi":
            make_pdf(p, pages=2)
        elif self.mode == "raise":
            (dest / "stray.pdf").write_text("junk")
            raise RuntimeError("boom")
        else:
            make_pdf(p, width=40 + 10 * len(self.profiles))
        return ScanResult(ok=True, partial=False, message="", output_paths=[str(p)])


class FakeOcr:
    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, inp, out, languages):
        self.calls.append((Path(inp), Path(out), list(languages)))
        if self.fail:
            Path(out).write_bytes(b"partial")
            raise OcrFailed("no ocrmypdf")
        Path(out).write_bytes(Path(inp).read_bytes())


@pytest.fixture
def env(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    run, ocr, clock = FakeRun(), FakeOcr(), Clock()
    col = MacCollector(tmp_path / "col", tmp_path / "scratch", timeout_seconds=600,
                       run=run, ocr=ocr, clock=clock)
    profile = Profile(name="Hardware Button", destination=str(dest), scanner="sane:x")
    return col, run, ocr, clock, profile, dest, tmp_path


def test_scan_page_adds_and_cleans(env):
    col, run, _, _, profile, _, tmp = env
    out = col.scan_page(profile, {"color_mode": "gray", "scanner": "sane:8800"})
    assert (out.ok, out.pages, out.first_page, out.message) == (True, 1, True, "")
    p = run.profiles[0]
    assert p.destination != profile.destination and p.destination.startswith(str(tmp / "scratch"))
    assert p.skip_ocr and p.format == "pdf" and p.color_mode == "gray" and p.scanner == "sane:8800"
    assert not Path(p.destination).exists()
    out = col.scan_page(profile, {})
    assert (out.pages, out.first_page) == (2, False)


def test_first_page_stores_settings(env):
    col, _, ocr, _, profile, dest, _ = env
    col.scan_page(profile, {})
    s = col._collection.settings
    assert s.destination == str(dest) and s.ocr_languages == ["deu", "eng"] and s.scanner == "sane:x"
    other = replace(profile, destination=str(dest / "elsewhere"))
    col.scan_page(other, {})
    assert col._collection.settings.destination == str(dest)
    assert col.finish().path.parent == dest


def test_failed_scan(env):
    col, run, _, _, profile, _, tmp = env
    col.scan_page(profile, {})
    run.mode = "fail"
    out = col.scan_page(profile, {})
    assert (out.ok, out.message, out.pages) == (False, "Cover open", 1)
    assert col.page_count == 1
    assert not Path(run.profiles[-1].destination).exists()
    run.mode = "ok"
    col.scan_page(profile, {})
    assert widths(col.finish().path) == [50, 70]


def test_run_raising(env):
    col, run, _, _, profile, _, tmp = env
    run.mode = "raise"
    out = col.scan_page(profile, {})
    assert out.ok is False and "boom" in out.message and col.page_count == 0
    assert not Path(run.profiles[-1].destination).exists()
    assert len(list((tmp / "scratch" / "kept").glob("*stray.pdf"))) == 1


def test_multipage_result_is_parked(env):
    col, run, _, _, profile, _, tmp = env
    run.mode = "multi"
    out = col.scan_page(profile, {})
    assert out.ok is False and col.page_count == 0
    assert len(list((tmp / "scratch" / "kept").glob("*.pdf"))) == 1


def test_not_expired_while_busy(env):
    col, run, _, clock, profile, _, _ = env
    col.scan_page(profile, {})
    seen = []
    inner = run.__call__

    def slow(p):
        clock.t += 10_000
        seen.append(col.expired())
        return inner(p)

    col._run = slow
    col.scan_page(profile, {})
    assert seen == [False]
    clock.t += 600
    assert col.expired()


@pytest.mark.parametrize("mode", ["nofile", "notpdf"])
def test_unusable_result(env, mode):
    col, run, _, _, profile, _, tmp = env
    run.mode = mode
    out = col.scan_page(profile, {})
    assert out.ok is False and col.page_count == 0
    kept = list((tmp / "scratch" / "kept").glob("*")) if mode == "notpdf" else []
    assert len(kept) == (1 if mode == "notpdf" else 0)
    assert not Path(run.profiles[-1].destination).exists()


def test_finish_merges_and_ocrs(env):
    col, _, ocr, clock, profile, dest, _ = env
    for _ in range(3):
        col.scan_page(profile, {})
    out = col.finish()
    assert out.path.parent == dest and out.path.name.startswith("scan_") and out.path.suffix == ".pdf"
    assert (out.pages, out.searchable, out.message) == (3, True, "")
    assert len(widths(out.path)) == 3 and widths(out.path) == sorted(widths(out.path)) == [50, 60, 70]
    assert len(ocr.calls) == 1 and ocr.calls[0][2] == ["deu", "eng"]
    assert col.page_count == 0 and not col.is_open
    assert not list(dest.glob("*.tmp"))
    for _ in range(2):
        col.scan_page(profile, {})
    out2 = col.finish()
    assert out2.path.name == out.path.name.replace(".pdf", "_2.pdf")


def test_skip_ocr(env):
    col, _, ocr, _, profile, _, _ = env
    col.scan_page(replace(profile, skip_ocr=True), {})
    out = col.finish()
    assert out.path and not out.searchable and out.message == "" and ocr.calls == []


def test_ocr_failure_keeps_pdf(env):
    col, _, ocr, _, profile, _, _ = env
    ocr.fail = True
    col.scan_page(profile, {})
    out = col.finish()
    assert out.path and out.path.exists() and not out.searchable and out.message
    assert col.page_count == 0
    assert not list(out.path.parent.glob("*.tmp"))
    assert [p.name for p in out.path.parent.iterdir()] == [out.path.name]


def test_missing_destination_keeps_collection(env):
    col, _, _, _, profile, dest, _ = env
    gone = dest / "later"
    col.scan_page(replace(profile, destination=str(gone)), {})
    col.scan_page(replace(profile, destination=str(gone)), {})
    out = col.finish()
    assert out.path is None and out.message and col.page_count == 2
    gone.mkdir()
    out = col.finish()
    assert out.path and out.pages == 2 and col.page_count == 0


def test_finish_empty(env):
    col, _, _, _, _, dest, _ = env
    out = col.finish()
    assert out.path is None and list(dest.iterdir()) == []


def test_expired(env):
    col, _, _, clock, profile, _, _ = env
    clock.t += 10_000
    assert not col.expired()
    col.scan_page(profile, {})
    clock.t += 599
    assert not col.expired()
    clock.t += 1
    assert col.expired()


def test_restart(env):
    col, run, ocr, clock, profile, dest, tmp = env
    col.scan_page(profile, {})
    clock.t += 700
    again = MacCollector(tmp / "col", tmp / "scratch", timeout_seconds=600, run=run, ocr=ocr, clock=clock)
    assert again.page_count == 1 and again.expired()
    assert again.finish().path.parent == dest


def test_discard(env):
    col, _, _, _, profile, _, tmp = env
    col.scan_page(profile, {})
    col.discard()
    assert col.page_count == 0 and not col.is_open
    assert list((tmp / "col").iterdir()) == []


def test_load_collect_timeout(tmp_path):
    p = tmp_path / "c.json"
    assert load_collect_timeout(p) == 10
    for raw, want in [('{"collect_timeout_minutes": 3}', 3), ('{"collect_timeout_minutes": 0}', 10),
                      ('{"collect_timeout_minutes": -1}', 10), ('{"collect_timeout_minutes": "x"}', 10),
                      ('{"collect_timeout_minutes": 2.5}', 10), ("{broken", 10), ("[]", 10)]:
        p.write_text(raw)
        assert load_collect_timeout(p) == want
