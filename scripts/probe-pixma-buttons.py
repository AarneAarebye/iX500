#!/usr/bin/env python3
"""Manual probe, not a test: what does a pixma scanner (the CanoScan 8800F)
report when each of its buttons is pressed?

Run with the scanner attached and nothing else using it:
    .venv/bin/python scripts/probe-pixma-buttons.py
It polls the way the menu bar app does (open, read the `target` option, close)
and the Mac speaks the name of the button to press at each cue. Printed: every
change of the options, so the press shows up next to its cue.

Result on the real 8800F (2026-10-24): `target` reads the pressed button's number
for about one poll and 0 otherwise: 1 COPY, 2 PHOTO/FILM, 3 E-MAIL, 4 COLOR,
5 BLACK & WHITE, 6 CUSTOM, 7 FINISH. `button-update` can't be set through
python-sane (SANE_TYPE_BUTTON), and `button-1` blips to 1 about every 12 s while
idle, so only `target` is used."""

import subprocess
import threading
import time

import sane

BUTTONS = [("COPY", 40), ("PHOTO FILM", 52), ("E-MAIL", 64), ("COLOR", 76), ("BLACK AND WHITE", 88),
           ("CUSTOM", 100), ("FINISH", 112)]
DURATION = 128
OPTIONS = ("button-1", "button-2", "target", "original")


def cue(label: str, at: int, t0: float) -> None:
    time.sleep(max(0, at - (time.time() - t0)))
    print(f"+{time.time() - t0:5.1f}s ===== CUE: press {label} now =====", flush=True)
    subprocess.run(["afplay", "/System/Library/Sounds/Ping.aiff"], check=False)
    subprocess.run(["say", f"Press {label} now"], check=False)


def main() -> int:
    sane.init()
    name = next((d[0] for d in sane.get_devices() if d[0].startswith("pixma:") and "8800f" in d[2].lower()), None)
    if name is None:
        print("No CanoScan 8800F found")
        return 1
    print("device", name, flush=True)
    t0 = time.time()
    for label, at in BUTTONS:
        threading.Thread(target=cue, args=(label, at, t0), daemon=True).start()
    last = None
    while time.time() - t0 < DURATION:
        try:
            dev = sane.open(name)
            try:
                opts = {o[1]: o[0] for o in dev.get_options()}  # fresh descriptors every time
                values = {k: dev.dev.get_option(opts[k]) for k in OPTIONS}
            finally:
                dev.close()
        except Exception as e:  # noqa: BLE001 - say what happened, keep going
            values = {"error": repr(e)}
        if values != last:
            print(f"+{time.time() - t0:5.1f}s", values, flush=True)
            last = values
        time.sleep(1.0)
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
