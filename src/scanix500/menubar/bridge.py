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

from scanix500 import __version__
from scanix500.menubar.ocr_jobs import OcrJobQueue, default_ocr_jobs_path
from scanix500.menubar.pairing import PairingError, PairingStore, bearer_token
from scanix500.menubar.profiles import Profile
from scanix500.menubar.protocol_v2 import IX500, ProtocolError, error_body, profile_for, response_from_result, validate
from scanix500.menubar.runner import ScanResult

DEFAULT_PORT = 8765
MAX_BODY_BYTES = 1_000_000
JSON_TYPE = "application/json; charset=utf-8"
# Each endpoint's one method (PROTOCOL.md "Endpoints and methods").
ENDPOINTS = {"/health": "GET", "/scanners": "GET", "/scan": "POST", "/pair": "POST",
             "/ocr/<job>": "GET"}
NOT_PAIRED_MESSAGE = (
    "This browser isn't paired with scanix500. Choose \u201cPair a Browser\u2026\u201d in the scanix500 menu "
    "and enter the code in Dossiary."
)

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


class V2Backend(Protocol):
    """Where version 2 scanners come from: the real iX500 (RealBackend) or
    the protocol's fake scanners (fakes.FakeBackend)."""

    def scanners(self) -> list[dict]: ...
    def scan(self, scanner: dict, settings: dict) -> tuple[int, dict]: ...
    def ocr_job(self, job_id: str) -> tuple[int, dict]: ...


class RealBackend:
    def __init__(self, get_destination: Callable[[], str], trigger: ScanTrigger,
                 ocr_jobs: OcrJobQueue | None | bool = None):
        self._get_destination = get_destination
        self._trigger = trigger
        # None: a queue is made lazily on first use; False: no background OCR at all.
        self._no_ocr = ocr_jobs is False
        self._queue = None if ocr_jobs is False else ocr_jobs
        self._queue_lock = threading.Lock()

    @property
    def _ocr_jobs(self) -> OcrJobQueue:
        # Created and started on first use when none was given, so nothing
        # touches the real application-support folder until a job exists.
        with self._queue_lock:
            if self._queue is None:
                self._queue = OcrJobQueue(default_ocr_jobs_path())
                self._queue.start()
            return self._queue

    def scanners(self) -> list[dict]:
        entry = dict(IX500)
        if self._no_ocr:
            del entry["ocr"]
        return [entry]

    def scan(self, scanner: dict, settings: dict) -> tuple[int, dict]:
        # ScanBusyError propagates to the handler (409).
        result = self._trigger.trigger(profile_for(settings, self._get_destination()))
        status, body = response_from_result(result)
        if status == 200 and settings.get("ocr"):
            by_name = {Path(p).name: p for p in result.output_paths}
            for entry in body.get("files", []):
                path = by_name.get(entry["name"])
                if path:
                    entry["ocrJob"] = self._ocr_jobs.submit(Path(path), entry["name"], entry["pages"],
                                                            settings["ocrLanguages"], copy_to=path)
        return status, body

    def ocr_job(self, job_id: str) -> tuple[int, dict]:
        if self._no_ocr:
            return 404, error_body("This OCR job is unknown, already fetched, or expired.")
        return self._ocr_jobs.fetch(job_id)


class _BodyError(ValueError):
    pass


def make_handler_class(
    get_destination: Callable[[], str], trigger: ScanTrigger | None, pairing: PairingStore, backend: V2Backend
) -> type[BaseHTTPRequestHandler]:
    """Builds a BaseHTTPRequestHandler bound to a live destination-folder
    getter (read fresh on every version 1 request -- the "Set Bridge Scan
    Folder..." setting can change while the bridge runs), the version 1
    ScanTrigger (None turns version 1 off, as in fake mode) and the
    version 2 backend."""

    class Handler(BaseHTTPRequestHandler):
        def _cors_headers(self) -> None:
            # Echo an allowed Origin (never "*"); a request without an
            # Origin header gets none. Refused origins never reach here.
            origin = self.headers.get("Origin")
            if origin is not None and origin_allowed(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def _send_json(self, status: int, body: dict, extra_headers: dict | None = None) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", JSON_TYPE)
            self.send_header("Content-Length", str(len(payload)))
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self._cors_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _error(self, status: int, message: str, allow: str | None = None) -> None:
            self._send_json(status, error_body(message), {"Allow": allow} if allow else None)

        def _origin_ok(self) -> bool:
            if origin_allowed(self.headers.get("Origin")):
                return True
            # No CORS headers on this 403 (PROTOCOL.md "Origins").
            payload = json.dumps(error_body("This origin may not use the scan bridge.")).encode("utf-8")
            self.send_response(403)
            self.send_header("Content-Type", JSON_TYPE)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
            return False

        def _read_json(self) -> object:
            raw = self.headers.get("Content-Length")
            if raw is None:
                data = b""
            else:
                try:
                    length = int(raw)
                except ValueError:
                    raise _BodyError() from None
                if length < 0 or length > MAX_BODY_BYTES:
                    raise _BodyError()
                data = self.rfile.read(length)
            try:
                return json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise _BodyError() from None

        def send_error(self, code, message=None, explain=None):  # noqa: N802 - BaseHTTPRequestHandler's own naming
            # Overridden so every response -- including the fallback path
            # for malformed requests that BaseHTTPRequestHandler produces on
            # its own -- carries the CORS header.
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

        def _preflight(self) -> None:
            self.send_response(204)
            self._cors_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Max-Age", "600")
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _dispatch(self) -> None:
            if not self._origin_ok():
                return
            if self.command == "OPTIONS":
                self._preflight()
                return
            url = urlsplit(self.path)
            path, job_id = url.path, ""
            if path.startswith("/ocr/") and len(path) > len("/ocr/") and "/" not in path[len("/ocr/"):]:
                job_id, path = path[len("/ocr/"):], "/ocr/<job>"
            method = ENDPOINTS.get(path)
            if method is None:
                self._error(404, "There's nothing at this address.")
                return
            if self.command != method:
                self._error(405, f"{url.path} only answers {method}.", allow=f"{method}, OPTIONS")
                return
            token_ok = pairing.is_valid(bearer_token(self.headers.get("Authorization")))
            if url.path == "/health":
                self._send_json(200, {"ok": True, "helper": "scanix500", "version": __version__, "protocol": 2,
                                      "paired": token_ok, "service": "scanix500-bridge"})
                return
            if url.path == "/pair":
                self._handle_pair()
                return
            if url.path == "/scan" and url.query and trigger is not None:
                self._handle_v1_scan(url.query, token_ok)
                return
            if not token_ok:
                self._error(401, NOT_PAIRED_MESSAGE)
                return
            if url.path == "/scanners":
                self._send_json(200, {"scanners": backend.scanners()})
                return
            if url.path.startswith("/ocr/"):
                status, response = backend.ocr_job(job_id)
                self._send_json(status, response)
                return
            self._handle_v2_scan()

        def _handle_v1_scan(self, query: str, token_ok: bool) -> None:
            # Version 1 (Dossiary before 1.44): query parameters, {"error"} bodies.
            if not token_ok:
                self._send_json(401, {"error": "this browser isn't paired; choose \u201cPair a Browser\u2026\u201d in the scanix500 menu"})
                return
            params = {key: values[0] for key, values in parse_qs(query).items()}
            status, body = route_scan_request(get_destination(), params, trigger)
            self._send_json(status, body)

        def _handle_v2_scan(self) -> None:
            try:
                body = self._read_json()
            except _BodyError:
                self._error(400, "The scan request must be a JSON object.")
                return
            try:
                scanner, settings = validate(body, backend.scanners())
            except ProtocolError as e:
                self._error(e.status, e.message)
                return
            try:
                status, response = backend.scan(scanner, settings)
            except ScanBusyError:
                self._error(409, "A scan is already running. Wait for it to finish, then try again.")
                return
            except Exception:  # noqa: BLE001 - always answer, never drop the connection
                self._error(500, "The scan failed unexpectedly.")
                return
            self._send_json(status, response)

        def _handle_pair(self) -> None:
            try:
                body = self._read_json()
            except _BodyError:
                self._error(400, "The request body must be JSON like {\"code\": \"123456\"}.")
                return
            if not isinstance(body, dict) or not isinstance(body.get("code"), str):
                self._error(400, "The request needs a \"code\" string.")
                return
            if "client" in body and (not isinstance(body["client"], str) or len(body["client"]) > 200):
                self._error(400, "\"client\" must be text of at most 200 characters.")
                return
            try:
                token = pairing.pair(body["code"])
            except PairingError as err:
                self._error(403, str(err))
                return
            self._send_json(200, {"ok": True, "token": token})

        # Every method goes through _dispatch. Bodies are read per endpoint;
        # an unread body is safe because protocol_version stays the default
        # HTTP/1.0 (each connection closes after one response).
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own naming
            self._dispatch()

        do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_GET  # noqa: N815

        def log_message(self, format: str, *args: object) -> None:
            # scanix500-menubar has no console under launchd -- keep quiet
            # rather than writing to a stderr nothing reads.
            pass

    return Handler


def start_bridge_server(
    get_destination: Callable[[], str],
    trigger: ScanTrigger | None,
    pairing: PairingStore,
    port: int | None = None,
    backend: V2Backend | None = None,
) -> ThreadingHTTPServer:
    """Starts the bridge listening on 127.0.0.1:<port> in a daemon thread
    and returns the live server. port resolution order: this parameter (if
    given) > SCANIX500_BRIDGE_PORT env var > DEFAULT_PORT. backend=None
    means the real iX500 (RealBackend)."""
    resolved_port = port if port is not None else int(os.environ.get("SCANIX500_BRIDGE_PORT", DEFAULT_PORT))
    backend = backend or RealBackend(get_destination, trigger)
    handler_cls = make_handler_class(get_destination, trigger, pairing, backend)
    server = ThreadingHTTPServer(("127.0.0.1", resolved_port), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
