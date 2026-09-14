from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import unquote, urlsplit

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

        def send_error(self, code, message=None, explain=None):  # noqa: N802 - BaseHTTPRequestHandler's own naming
            # Overridden so every response -- including the fallback path
            # for unhandled methods (GET, PUT, ...) that BaseHTTPRequestHandler
            # produces on its own -- carries the CORS header, per this
            # bridge's own "every response, success or error" contract.
            # Mirrors the stdlib implementation's own message/explain
            # defaulting (self.responses[code]) rather than leaving an
            # unhandled-method response with an empty body.
            try:
                shortmsg, longmsg = self.responses[code]
            except KeyError:
                shortmsg, longmsg = "???", "???"
            if message is None:
                message = shortmsg
            if explain is None:
                explain = longmsg
            self.send_response(code, message)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "text/html;charset=utf-8")
            body = f"{message}: {explain}".encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own naming
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            # Never reads self.rfile -- safe only because protocol_version
            # stays the default HTTP/1.0 (each connection closes after one
            # response, so there's no leftover unread body to corrupt a
            # later request on the same connection); revisit if this is
            # ever bumped to HTTP/1.1 with request bodies in play.
            prefix = "/scan/"
            path = urlsplit(self.path).path
            if not path.startswith(prefix):
                self._send_json(404, {"error": "not found"})
                return
            name = unquote(path[len(prefix):])
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
