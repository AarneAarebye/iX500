import threading
import time

from scanix500.server.button_events import ButtonEvents


def test_snapshot_before_and_after_presses():
    ev = ButtonEvents()
    assert ev.snapshot() == {"seq": 0}
    ev.record("color")
    ev.record("finish")
    assert ev.snapshot() == {"seq": 2, "button": "finish"}


def test_command_runs_with_the_button_name_and_no_shell():
    calls = []
    done = threading.Event()

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        done.set()

    ev = ButtonEvents(command=["/usr/bin/say", "-v", "x"], run=fake_run)
    ev.record("bw")
    assert done.wait(2)
    argv, kw = calls[0]
    assert argv == ["/usr/bin/say", "-v", "x", "bw"]
    assert kw["timeout"] == 5 and not kw.get("shell")


def test_failing_or_hanging_command_never_blocks_or_raises():
    def boom(argv, **kw):
        raise OSError("nope")

    ev = ButtonEvents(command=["x"], run=boom)
    started = time.time()
    ev.record("color")
    assert time.time() - started < 0.5
    assert ev.snapshot()["seq"] == 1


def test_no_command_runs_nothing():
    ev = ButtonEvents(command=None, run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    ev.record("custom")
