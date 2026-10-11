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
        if str(dst) == str(safety) + ".tmp":
            seen["fetch_during_copy"] = q.fetch(job)  # lock is free here
        return real(src, dst, *a, **k)

    monkeypatch.setattr(mod.shutil, "copyfile", spy)
    q.run_next()
    assert seen["fetch_during_copy"][0] == 202  # still not "done" while copying
    assert q.fetch(job)[0] == 200
    assert safety.read_bytes().endswith(b"%SEARCHABLE ")


def test_no_lost_wakeup_between_run_next_and_wait(tmp_path):
    done = threading.Event()

    def ocr(src, dst, languages):
        fake_ocr(src, dst, languages)
        if Path(src).read_bytes() == b"%PDF-1.4 second":
            done.set()

    q, _ = make(tmp_path, ocr=ocr)
    second = tmp_path / "second.pdf"
    second.write_bytes(b"%PDF-1.4 second")
    orig = q.run_next
    injected = []

    def hooked():
        r = orig()
        if not r and not injected:
            injected.append(q.submit(second, "second.pdf", 1, []))  # right after "nothing queued"
        return r

    q.run_next = hooked
    q.start()
    try:
        q.submit(plain(tmp_path), "scan.pdf", 1, [])
        assert done.wait(timeout=10)  # far below the 60 s wait timeout
    finally:
        q.stop()


def test_worker_survives_an_exception_and_stop_then_start_works(tmp_path, monkeypatch):
    import scanix500.menubar.ocr_jobs as mod
    monkeypatch.setattr(mod, "RETRY_SECONDS", 0.05)
    done = threading.Event()

    def ocr(src, dst, languages):
        fake_ocr(src, dst, languages)
        done.set()

    q, _ = make(tmp_path, ocr=ocr)
    orig = q.run_next
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full")
        return orig()

    q.run_next = flaky
    q.start()
    q.submit(plain(tmp_path), "scan.pdf", 1, [])
    assert done.wait(timeout=10)
    q.stop()
    done.clear()
    q.start()  # restart after stop
    try:
        q.submit(plain(tmp_path), "again.pdf", 1, [])
        assert done.wait(timeout=10)
    finally:
        q.stop()


def test_broken_meta_is_skipped_not_fatal(tmp_path):
    q, clock = make(tmp_path)
    good = q.submit(plain(tmp_path), "scan.pdf", 1, [])
    bad = tmp_path / "jobs" / ("b" * 22)
    bad.mkdir()
    (bad / "meta.json").write_text(json.dumps({"name": "x"}))
    assert q.fetch("b" * 22)[0] == 404
    assert q.run_next() is True and q.fetch(good)[0] == 200


def test_expiry_skips_running_jobs(tmp_path):
    q, clock = make(tmp_path)
    job = q.submit(plain(tmp_path), "scan.pdf", 1, [])
    meta = tmp_path / "jobs" / job / "meta.json"
    data = json.loads(meta.read_text())
    data["state"] = "running"
    meta.write_text(json.dumps(data))
    clock.now += 8 * 24 * 3600
    assert q.fetch(job)[0] == 202
    assert (tmp_path / "jobs" / job).exists()


def test_orphan_dirs_without_meta_are_swept_when_old(tmp_path):
    import os
    q, clock = make(tmp_path)
    old, fresh = tmp_path / "jobs" / ("o" * 22), tmp_path / "jobs" / ("f" * 22)
    for d in (old, fresh):
        d.mkdir()
        (d / "input.pdf").write_bytes(b"x")
    os.utime(old, (clock.now - 120, clock.now - 120))
    os.utime(fresh, (clock.now - 5, clock.now - 5))
    q.fetch("z" * 22)  # any call runs the sweep
    assert not old.exists() and fresh.exists()


def test_done_job_with_missing_result_answers_500(tmp_path):
    q, _ = make(tmp_path)
    job = q.submit(plain(tmp_path), "scan.pdf", 1, [])
    q.run_next()
    (tmp_path / "jobs" / job / "result.pdf").unlink()
    status, body = q.fetch(job)
    assert status == 500 and body["ok"] is False
    assert q.fetch(job)[0] == 404


def test_copy_to_is_atomic_and_leaves_no_tmp(tmp_path, monkeypatch):
    import scanix500.menubar.ocr_jobs as mod
    q, _ = make(tmp_path)
    safety = plain(tmp_path, "safety.pdf")
    q.submit(safety, "safety.pdf", 1, [], copy_to=str(safety))
    replaced = []
    real = mod.os.replace
    monkeypatch.setattr(mod.os, "replace", lambda a, b: (replaced.append((str(a), str(b))), real(a, b))[1])
    q.run_next()
    assert (str(safety) + ".tmp", str(safety)) in replaced
    assert not Path(str(safety) + ".tmp").exists()
    assert safety.read_bytes().endswith(b"%SEARCHABLE ")


def test_failed_final_write_marks_job_failed(tmp_path, monkeypatch):
    q, _ = make(tmp_path)
    job = q.submit(plain(tmp_path), "scan.pdf", 1, [])
    real = q._write_meta
    n = []

    def flaky(job_id, meta):
        n.append(meta["state"])
        if meta["state"] == "done":
            raise OSError("disk full")
        return real(job_id, meta)

    monkeypatch.setattr(q, "_write_meta", flaky)
    q.run_next()
    assert q.fetch(job)[0] == 500


def test_copy_to_left_alone_when_moved_away(tmp_path):
    q, _ = make(tmp_path)
    safety = plain(tmp_path, "safety.pdf")
    q.submit(safety, "safety.pdf", 1, [], copy_to=str(safety))
    safety.unlink()
    q.run_next()
    assert not safety.exists() and not Path(str(safety) + ".tmp").exists()


def test_copy_to_left_alone_when_changed(tmp_path):
    q, _ = make(tmp_path)
    safety = plain(tmp_path, "safety.pdf")
    q.submit(safety, "safety.pdf", 1, [], copy_to=str(safety))
    safety.write_bytes(b"%PDF-1.4 edited by hand")
    q.run_next()
    assert safety.read_bytes() == b"%PDF-1.4 edited by hand"


def test_input_is_removed_once_finished(tmp_path):
    q, _ = make(tmp_path)
    job = q.submit(plain(tmp_path), "a.pdf", 1, [])
    q.run_next()
    assert not (tmp_path / "jobs" / job / "input.pdf").exists()
    assert (tmp_path / "jobs" / job / "result.pdf").exists()
    q2, _ = make(tmp_path / "x", ocr=lambda s, d, l: (_ for _ in ()).throw(OcrFailed("no")))
    (tmp_path / "x").mkdir(exist_ok=True)
    job2 = q2.submit(plain(tmp_path), "b.pdf", 1, [])
    q2.run_next()
    assert not (tmp_path / "x" / "jobs" / job2 / "input.pdf").exists()


def test_start_expires_old_jobs(tmp_path):
    q, clock = make(tmp_path)
    job = q.submit(plain(tmp_path), "a.pdf", 1, [])
    clock.now += 8 * 24 * 3600
    q.start()
    try:
        for _ in range(100):
            if not (tmp_path / "jobs" / job).exists():
                break
            threading.Event().wait(0.05)
        assert not (tmp_path / "jobs" / job).exists()
    finally:
        q.stop()
