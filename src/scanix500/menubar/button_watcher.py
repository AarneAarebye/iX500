# src/scanix500/menubar/button_watcher.py
from __future__ import annotations

from collections.abc import Callable

from scanix500.capture import find_canoscan_device, find_fujitsu_device
from scanix500.scanners import CANOSCAN_8800F_ID, IX500_ID

_FINDERS = {IX500_ID: find_fujitsu_device, CANOSCAN_8800F_ID: find_canoscan_device}


def resolve_device_name(scanner_id: str = IX500_ID) -> str | None:
    """Finds a scanner's SANE device name, or None if it can't be found
    right now (not installed, not plugged in, transient failure, unknown
    scanner). Safe to call repeatedly — never raises."""
    finder = _FINDERS.get(scanner_id)
    if finder is None:
        return None
    try:
        import sane
    except ImportError:
        return None
    try:
        sane.init()
        devices = sane.get_devices()
    except Exception:
        return None
    return finder(devices)


def button_pressed(device_name: str) -> bool:
    """Opens the device, checks whether the physical Scan button is
    currently pressed, and closes it again. Returns False on any failure
    (device busy, unplugged, transient error) rather than raising — a
    poller must never crash the app over a momentary hardware hiccup.

    Re-fetches the option descriptor list before reading the raw value:
    confirmed against real hardware that a stale/cached descriptor set
    does not reflect a fresh button press. Reads the raw option value via
    dev.dev.get_option(index) rather than dev.scan, since SaneDev.scan is
    python-sane's own bound method (for triggering a scan) and shadows the
    SANE option of the same name.
    """
    try:
        import sane

        sane.init()
        dev = sane.open(device_name)
        try:
            opts = dev.get_options()
            scan_opt = next((o for o in opts if o[1] == "scan"), None)
            if scan_opt is None:
                return False
            return bool(dev.dev.get_option(scan_opt[0]))
        finally:
            dev.close()
    except Exception:
        return False


# The 8800F's buttons (measured on the real scanner, see the spec's section 5):
# SANE's `target` option reads the pressed button's number for about one poll,
# 0 otherwise. 1 COPY, 2 PHOTO/FILM, 3 E-MAIL and 7 FINISH do nothing; the three
# PDF scan buttons start a scan, COLOR and BLACK & WHITE choosing the mode and
# CUSTOM keeping the profile as it is.
PIXMA_BUTTONS: dict[int, dict] = {4: {"color_mode": "color"}, 5: {"color_mode": "bw"}, 6: {}}


def _pixma_overrides(values: dict[str, int]) -> dict | None:
    """The Profile overrides the pressed button asks for ({} = none), or None
    when no scan button is pressed. Only `target` counts: `button-1` blips to 1
    about every 12 s while the scanner is idle."""
    found = PIXMA_BUTTONS.get(values.get("target", 0))
    return None if found is None else dict(found)


def pixma_button_overrides(device_name: str) -> dict | None:
    """Opens the 8800F, reads its `target` option and closes it again.
    Returns None on any failure rather than raising, like button_pressed().
    button-update is never called: python-sane can't set a button option, and
    a fresh open sees the current state."""
    try:
        import sane

        sane.init()
        dev = sane.open(device_name)
        try:
            opts = {o[1]: o[0] for o in dev.get_options()}  # fresh descriptors, as in button_pressed()
            return _pixma_overrides({"target": dev.dev.get_option(opts["target"])})
        finally:
            dev.close()
    except Exception:
        return None


def ix500_button_overrides(device_name: str) -> dict | None:
    """The iX500 has one Scan button: pressed means a scan with the profile as it is."""
    return {} if button_pressed(device_name) else None


# Which function reads each scanner's buttons. The Mac app polls all of them.
BUTTON_READERS: dict[str, Callable[[str], dict | None]] = {
    IX500_ID: ix500_button_overrides,
    CANOSCAN_8800F_ID: pixma_button_overrides,
}
