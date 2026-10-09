from __future__ import annotations

import base64
import json
import os
import re
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from scanix500.menubar.pairing import PairingError, PairingStore, bearer_token
from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult

DEFAULT_PORT = 8765
MAX_BODY_BYTES = 10_000

_ALLOWED_ORIGIN = re.compile(r"http://(localhost|127\.0\.0\.1)(?::([0-9]{1,5}))?")


def origin_allowed(origin: str | None) -> bool:
    """Only Dossiary's own origins may use the bridge: no Origin header
    (curl, tests), "null" (a file:// page), or plain-http localhost /
    127.0.0.1 on a port 1-65535. Anything else -- any other website -- gets
    403. "null" alone can't prove the caller is Dossiary (sandboxed iframes
    send it too), which is why scans also need a paired token."""
    if origin is None or origin == "null":
        return True
    match = _ALLOWED_ORIGIN.fullmatch(origin)
    if not match:
        return False
    port = match.group(2)
    return port is None or 1 <= int(port) <= 65535


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


def _encode_files(output_paths: list[str]) -> list[dict]:
    """Reads each path in output_paths off disk and base64-encodes it for
    the HTTP response, in the same order as output_paths -- though
    possibly shorter than it, if any path was skipped, so a caller should
    match entries by filename, not by index. A path that can no longer be
    read (removed, permissions) is skipped rather than raising -- the
    response still carries whatever files it could read, and
    output_paths / the on-disk safety-net copy in the destination folder
    remain the authoritative record regardless."""
    files = []
    for path in output_paths:
        try:
            data = Path(path).read_bytes()
        except OSError:
            continue
        files.append({"filename": Path(path).name, "content_base64": base64.b64encode(data).decode("ascii")})
    return files


def _parse_bool_param(params: dict[str, str], name: str) -> bool | None:
    """Returns True/False for a query parameter whose value is exactly
    the literal string 'true' or 'false'; None if the parameter is
    missing or holds any other value, so the caller can report exactly
    which parameter(s) were invalid rather than a generic failure."""
    value = params.get(name)
    if value not in ("true", "false"):
        return None
    return value == "true"


def route_scan_request(destination: str, params: dict[str, str], trigger: ScanTrigger) -> tuple[int, dict]:
    """Pure dispatch: validate the three required boolean scan parameters
    (skip_blank_filter, skip_ocr, split_on_blank -- each must be exactly
    'true' or 'false'), build an ephemeral Profile from them plus the
    already-resolved destination folder, run it via trigger, shape the
    response. No HTTP-specific code and no threading here. This replaces
    the old profile-name lookup entirely -- see the 2026-09-16
    parameterized-scan design spec. The constructed Profile's own `name`
    ("Dossiary Bridge Scan") is never persisted or shown in any menu; it
    exists only because Profile requires a name field."""
    parsed = {name: _parse_bool_param(params, name) for name in ("skip_blank_filter", "skip_ocr", "split_on_blank")}
    missing = [name for name, value in parsed.items() if value is None]
    if missing:
        return 400, {"error": f"missing or invalid parameter(s): {', '.join(missing)} (each must be 'true' or 'false')"}

    profile = Profile(
        name="Dossiary Bridge Scan",
        destination=destination,
        skip_blank_filter=parsed["skip_blank_filter"],
        skip_ocr=parsed["skip_ocr"],
        split_on_blank=parsed["split_on_blank"],
    )
    try:
        result = trigger.trigger(profile)
    except ScanBusyError:
        return 409, {"error": "a scan is already in progress"}
    return 200, {
        "ok": result.ok,
        "partial": result.partial,
        "message": result.message,
        "output_paths": result.output_paths,
        "files": _encode_files(result.output_paths),
    }


def make_handler_class(
    get_destination: Callable[[], str], trigger: ScanTrigger, pairing: PairingStore
) -> type[BaseHTTPRequestHandler]:
    """Builds a BaseHTTPRequestHandler bound to a live destination-folder
    getter (a zero-arg callable, not a static string -- the "Set Bridge
    Scan Folder..." setting can change via the menu while the bridge is
    running, and every request must see the current value) and a
    ScanTrigger."""

    class Handler(BaseHTTPRequestHandler):
        def _cors_headers(self) -> None:
            # Echo an allowed Origin (never "*"); a request without an
            # Origin header gets none. Refused origins never reach here.
            origin = self.headers.get("Origin")
            if origin is not None and origin_allowed(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def _send_json(self, status: int, body: dict) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(payload)

        def _origin_ok(self) -> bool:
            if origin_allowed(self.headers.get("Origin")):
                return True
            payload = json.dumps({"error": "this origin may not use the scan bridge"}).encode("utf-8")
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return False

        def _read_json_body(self) -> object:
            """The request body as parsed JSON; raises ValueError for a
            missing, oversized or malformed body."""
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                raise ValueError("bad Content-Length") from None
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("missing or oversized body")
            return json.loads(self.rfile.read(length).decode("utf-8"))

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
            self._cors_headers()
            self.send_header("Content-Type", "text/html;charset=utf-8")
            body = f"{message}: {explain}".encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own naming
            if not self._origin_ok():
                return
            self.send_response(204)
            self._cors_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Max-Age", "600")
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            # A separate, deliberately trivial route from do_POST's scan
            # trigger -- this must never touch trigger/self._scanning, so a
            # caller (Dossiary) can probe "is the bridge here" without any
            # risk of firing a real scan at an unknown port.
            if not self._origin_ok():
                return
            path = urlsplit(self.path).path
            if path == "/health":
                # "paired" tells Dossiary whether the token it sent (if any)
                # is valid, so it knows when to ask for a pairing code.
                token = bearer_token(self.headers.get("Authorization"))
                self._send_json(200, {"service": "scanix500-bridge", "paired": pairing.is_valid(token)})
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            # /scan never reads self.rfile (its parameters live in the query
            # string); only /pair reads a small JSON body. Leaving a body
            # unread is safe because protocol_version stays the default
            # HTTP/1.0: each connection closes after one response. Revisit
            # if this is ever bumped to HTTP/1.1.
            if not self._origin_ok():
                return
            path = urlsplit(self.path).path
            if path == "/pair":
                self._handle_pair()
                return
            if path != "/scan":
                self._send_json(404, {"error": "not found"})
                return
            if not pairing.is_valid(bearer_token(self.headers.get("Authorization"))):
                self._send_json(401, {"error": "this browser isn't paired; choose “Pair a Browser…” in the scanix500 menu"})
                return
            query = urlsplit(self.path).query
            params = {key: values[0] for key, values in parse_qs(query).items()}
            status, body = route_scan_request(get_destination(), params, trigger)
            self._send_json(status, body)

        def _handle_pair(self) -> None:
            try:
                body = self._read_json_body()
            except (ValueError, UnicodeDecodeError, RecursionError):
                self._send_json(400, {"error": "the request body must be JSON like {\"code\": \"123456\"}"})
                return
            if not isinstance(body, dict) or not isinstance(body.get("code"), str):
                self._send_json(400, {"error": "the request needs a \"code\" string"})
                return
            try:
                token = pairing.pair(body["code"])
            except PairingError as err:
                self._send_json(403, {"error": str(err)})
                return
            self._send_json(200, {"ok": True, "token": token})

        def log_message(self, format: str, *args: object) -> None:
            # scanix500-menubar has no console under launchd -- keep quiet
            # rather than writing to a stderr nothing reads.
            pass

    return Handler


def start_bridge_server(
    get_destination: Callable[[], str], trigger: ScanTrigger, pairing: PairingStore, port: int | None = None
) -> ThreadingHTTPServer:
    """Starts the bridge listening on 127.0.0.1:<port> in a daemon thread
    and returns the live server. port resolution order: this parameter (if
    given) > SCANIX500_BRIDGE_PORT env var > DEFAULT_PORT."""
    resolved_port = port if port is not None else int(os.environ.get("SCANIX500_BRIDGE_PORT", DEFAULT_PORT))
    handler_cls = make_handler_class(get_destination, trigger, pairing)
    server = ThreadingHTTPServer(("127.0.0.1", resolved_port), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
