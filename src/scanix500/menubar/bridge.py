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


import json
import os
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote


def make_handler_class(
    get_profiles: Callable[[], list[Profile]], trigger: ScanTrigger
) -> type[BaseHTTPRequestHandler]:
    """Builds a BaseHTTPRequestHandler bound to a live profiles getter (a
    zero-arg callable, not a static list -- profiles.json can change via
    Add/Edit/Delete Profile while the bridge is running, and every request
    must see the current list) and a ScanTrigger."""

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, body: dict) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own naming
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            prefix = "/scan/"
            if not self.path.startswith(prefix):
                self._send_json(404, {"error": "not found"})
                return
            name = unquote(self.path[len(prefix):])
            status, body = route_scan_request(get_profiles(), name, trigger)
            self._send_json(status, body)

        def log_message(self, format: str, *args: object) -> None:
            # scanix500-menubar has no console under launchd -- keep quiet
            # rather than writing to a stderr nothing reads.
            pass

    return Handler


def start_bridge_server(
    get_profiles: Callable[[], list[Profile]], trigger: ScanTrigger, port: int | None = None
) -> ThreadingHTTPServer:
    """Starts the bridge listening on 127.0.0.1:<port> in a daemon thread
    and returns the live server. port resolution order: this parameter (if
    given) > SCANIX500_BRIDGE_PORT env var > DEFAULT_PORT."""
    resolved_port = port if port is not None else int(os.environ.get("SCANIX500_BRIDGE_PORT", DEFAULT_PORT))
    handler_cls = make_handler_class(get_profiles, trigger)
    server = ThreadingHTTPServer(("127.0.0.1", resolved_port), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
