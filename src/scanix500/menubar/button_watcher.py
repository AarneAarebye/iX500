# src/scanix500/menubar/button_watcher.py
from __future__ import annotations

from scanix500.capture import find_fujitsu_device


def resolve_device_name() -> str | None:
    """Finds the iX500's SANE device name, or None if it can't be found
    right now (not installed, not plugged in, transient failure). Safe to
    call repeatedly — never raises."""
    try:
        import sane
    except ImportError:
        return None
    try:
        sane.init()
        devices = sane.get_devices()
    except Exception:
        return None
    return find_fujitsu_device(devices)


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
