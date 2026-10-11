import base64
import json
import threading
from pathlib import Path

import pytest

from scanix500.menubar.ocr_jobs import JOB_ID_RE, OcrJobQueue
from scanix500.pdf_builder import OcrFailed


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def fake_ocr(src, dst, languages):
    Path(dst).write_bytes(Path(src).read_bytes() + b"%SEARCHABLE " + "+".join(languages).encode())


def make(tmp_path, ocr=fake_ocr):
    clock = Clock()
    return OcrJobQueue(tmp_path / "jobs", ocr=ocr, clock=clock), clock


def plain(tmp_path, name="scan.pdf"):
    p = tmp_path / name
    p.write_bytes(b"%PDF-1.4 plain")
    return p


def test_job_lifecycle(tmp_path):
    q, _ = make(tmp_path)
    job = q.submit(plain(tmp_path), "scan.pdf", 2, ["deu"])
    assert JOB_ID_RE.fullmatch(job)
    assert q.fetch(job) == (202, {"ok": True, "state": "queued", "position": 0})
    assert q.run_next() is True
    status, body = q.fetch(job)
    assert status == 200 and body["ok"] is True
    [f] = body["files"]
    assert (f["name"], f["pages"]) == ("scan.pdf", 2)
    assert base64.b64decode(f["data"]).endswith(b"%SEARCHABLE deu")
    assert q.fetch(job)[0] == 404
    assert not (tmp_path / "jobs" / job).exists()


def test_order_and_position(tmp_path):
    q, clock = make(tmp_path)
    a = q.submit(plain(tmp_path, "a.pdf"), "a.pdf", 1, [])
    clock.now += 1
    b = q.submit(plain(tmp_path, "b.pdf"), "b.pdf", 1, [])
    assert q.fetch(b) == (202, {"ok": True, "state": "queued", "position": 1})
    q.run_next()
    assert q.fetch(b) == (202, {"ok": True, "state": "queued", "position": 0})
    assert q.fetch(a)[0] == 200


def test_failed_job_is_final(tmp_path):
    def broken(src, dst, languages):
        raise OcrFailed("ocrmypdf isn't installed")

    q, _ = make(tmp_path, ocr=broken)
    job = q.submit(plain(tmp_path), "scan.pdf", 1, [])
    q.run_next()
    status, body = q.fetch(job)
    assert status == 500 and body["ok"] is False and body["partial"] is False and "ocrmypdf" in body["message"]
    assert q.fetch(job)[0] == 404


def test_survives_a_restart_in_order(tmp_path):
    q, clock = make(tmp_path)
    a = q.submit(plain(tmp_path, "a.pdf"), "a.pdf", 1, [])
    clock.now += 1
    b = q.submit(plain(tmp_path, "b.pdf"), "b.pdf", 1, [])
    meta = tmp_path / "jobs" / a / "meta.json"
    data = json.loads(meta.read_text())
    data["state"] = "running"
    meta.write_text(json.dumps(data))  # crashed mid-job
    q2 = OcrJobQueue(tmp_path / "jobs", ocr=fake_ocr, clock=clock)
    assert q2.fetch(a) == (202, {"ok": True, "state": "queued", "position": 0})
    q2.run_next()
    assert q2.fetch(a)[0] == 200 and q2.fetch(b)[0] == 202


def test_expiry(tmp_path):
    q, clock = make(tmp_path)
    job = q.submit(plain(tmp_path), "scan.pdf", 1, [])
    clock.now += 7 * 24 * 3600 + 1
    assert q.fetch(job)[0] == 404
    assert not (tmp_path / "jobs" / job).exists()


@pytest.mark.parametrize("bad", ["../etc", "a", "", "x" * 21, "ab/cd" + "x" * 20, "..%2F" + "x" * 20])
def test_bad_ids_never_touch_the_disk(tmp_path, bad):
    q, _ = make(tmp_path)
    assert q.fetch(bad)[0] == 404


def test_copy_to_gets_the_searchable_version(tmp_path):
    q, _ = make(tmp_path)
    safety = plain(tmp_path, "safety.pdf")
    q.submit(safety, "safety.pdf", 1, [], copy_to=str(safety))
    q.run_next()
    assert safety.read_bytes().endswith(b"%SEARCHABLE ")


def test_worker_thread_processes_jobs(tmp_path):
    done = threading.Event()

    def signalling_ocr(src, dst, languages):
        fake_ocr(src, dst, languages)
        done.set()

    q, _ = make(tmp_path, ocr=signalling_ocr)
    q.start()
    try:
        job = q.submit(plain(tmp_path), "scan.pdf", 1, [])
        assert done.wait(timeout=10)
    finally:
        q.stop()  # joins the worker, so run_next() has fully finished
    assert q.fetch(job)[0] == 200  # fetch deletes on 200: exactly once
    assert q.fetch(job)[0] == 404


def test_copy_to_happens_before_the_job_is_fetchable(tmp_path, monkeypatch):
    import scanix500.menubar.ocr_jobs as mod
    q, _ = make(tmp_path)
    safety = plain(tmp_path, "safety.pdf")
    job = q.submit(safety, "safety.pdf", 1, [], copy_to=str(safety))
    seen = {}
    real = mod.shutil.copyfile

    def spy(src, dst, *a, **k):
        if str(dst) == str(safety):
            seen["fetch_during_copy"] = q.fetch(job)  # lock is free here
        return real(src, dst, *a, **k)

    monkeypatch.setattr(mod.shutil, "copyfile", spy)
    q.run_next()
    assert seen["fetch_during_copy"][0] == 202  # still not "done" while copying
    assert q.fetch(job)[0] == 200
    assert safety.read_bytes().endswith(b"%SEARCHABLE ")
