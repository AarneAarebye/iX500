from __future__ import annotations

from typing import Protocol

from scanix500.menubar.profiles import Profile, find_profile
from scanix500.menubar.runner import ScanResult

DEFAULT_PORT = 8765


class ScanBusyError(Exception):
    """Raised by a ScanTrigger.trigger() call when a scan is already
    running -- maps to an HTTP 409 in route_scan_request, never to a
    queued/blocked request."""


class ScanTrigger(Protocol):
    """Something that can run a named profile's scan and block until it
    completes (or raise ScanBusyError), returning the real ScanResult --
    not just "started". Implemented by ScanixMenuBarApp (see app.py,
    Task 3) for the real app; FakeScanTrigger (tests/menubar/test_bridge.py)
    stands in for tests. Deliberately has no rumps/AppKit/threading
    dependency in this file -- that all lives in app.py's implementation."""

    def trigger(self, profile: Profile) -> ScanResult: ...


def route_scan_request(profiles: list[Profile], name: str, trigger: ScanTrigger) -> tuple[int, dict]:
    """Pure dispatch: look up `name` in `profiles`, run it via `trigger`,
    shape the response. No HTTP-specific code and no threading here --
    Task 2's HTTP handler and Task 3's ScanixMenuBarApp.trigger() are the
    only two things that need to know this exists."""
    profile = find_profile(profiles, name)
    if profile is None:
        return 404, {"error": f"no profile named {name!r}"}
    try:
        result = trigger.trigger(profile)
    except ScanBusyError:
        return 409, {"error": "a scan is already in progress"}
    return 200, {
        "ok": result.ok,
        "partial": result.partial,
        "message": result.message,
        "output_paths": result.output_paths,
    }
