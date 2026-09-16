# Scan Bridge Parameterized Scan (scanix500 side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bridge's `POST /scan/<profile-name>` profile lookup
with a parameterized `POST /scan?skip_blank_filter=...&skip_ocr=...&split_on_blank=...`
endpoint, and add a new, small, menu-editable "bridge scan folder" setting
to replace the safety-net destination a profile used to carry.

**Architecture:** `route_scan_request()` stops taking a `profiles` list and
a `name` to look up; instead it takes the already-open destination folder
and a flat dict of query parameters, validates the three required boolean
flags itself, and builds an *ephemeral* `Profile` object on the fly to
pass into the unchanged `trigger.trigger(profile)` call — so
`ScanTrigger`, `ScanixMenuBarApp._execute_scan()`, `runner.run_scan()`,
and `_build_argv()` need zero changes. A new `bridge_settings.py` module
(mirroring `profiles.py`'s own load/save/atomic-write pattern, but for a
single string value) replaces the profile-carried `destination`. `app.py`
gains a new "Set Bridge Scan Folder…" menu item reusing the existing
native folder-picker already built for Add/Edit Profile.
`POST /scan/<profile-name>` is removed entirely — `profiles.json`, the
manual profile dropdown, and the reserved "Hardware Button" profile are
all untouched by this plan.

**Tech Stack:** Python 3.11+, stdlib `http.server`/`urllib.parse`, PyObjC
(`NSOpenPanel`, reused not rebuilt), `pytest`.

## Global Constraints

- `profiles.json`, `load_profiles()`/`save_profiles()`/`add_profile()`/
  `replace_profile()`/`delete_profile()`/`find_profile()`, the menu bar's
  manual profile dropdown, and the reserved `"Hardware Button"` profile
  are all out of scope for this plan — none of them change.
- `ScanTrigger`, `ScanixMenuBarApp.trigger()`/`_execute_scan()`/
  `_start_scan()`, `runner.run_scan()`/`_build_argv()`/
  `parse_scan_output()` are all out of scope — they already just take a
  `Profile` and don't need to know it's now constructed on the fly rather
  than looked up.
- `POST /scan/<profile-name>` is removed, not kept alongside the new
  endpoint (see the design spec's own "Scope" and "What this removes"
  sections for the reasoning).
- The three query parameters (`skip_blank_filter`, `skip_ocr`,
  `split_on_blank`) are each required and must be exactly the literal
  string `"true"` or `"false"`; anything missing or different is a `400
  Bad Request` naming which parameter(s) were the problem.
- `do_POST()` must continue to never read `self.rfile` — the existing
  comment explaining why that's safe only under HTTP/1.0 stays accurate
  and unchanged, since query-string parameters live entirely in the
  request line, not the body.
- `GET /health`, the `409` busy response, the response body shape
  (`ok`/`partial`/`message`/`output_paths`/`files`), and `_encode_files()`
  are all unchanged.
- The new bridge-scan-folder setting is read fresh on every request via a
  live callable (mirroring the existing `get_profiles` pattern this
  replaces) — not snapshotted at `start_bridge_server()` time — so
  changing it via the new menu item takes effect immediately without
  restarting the bridge.
- Full spec: `docs/superpowers/specs/2026-09-16-scan-bridge-parameterized-scan-design.md`
  (in the sibling Dossiary repo,
  `/Users/aarneaarebye/Projects/Paperless/Dossiary`). This plan covers
  only that spec's scanix500-side pieces; the Dossiary-side URL
  construction change is a separate plan in that other repo.

---

### Task 1: `route_scan_request()` and `do_POST()` — parameterized scan, no profile lookup

**Files:**
- Modify: `src/scanix500/menubar/bridge.py` (replace entire file content)
- Modify: `tests/menubar/test_bridge.py` (replace entire file content)

**Interfaces:**
- Consumes: `Profile` (unchanged, from `profiles.py`), `ScanResult`
  (unchanged, from `runner.py`), `ScanTrigger`/`ScanBusyError` (unchanged,
  defined in this file).
- Produces: `route_scan_request(destination: str, params: dict[str, str],
  trigger: ScanTrigger) -> tuple[int, dict]` (new signature — was
  `(profiles: list[Profile], name: str, trigger: ScanTrigger)`),
  `_parse_bool_param(params: dict[str, str], name: str) -> bool | None`
  (new), `make_handler_class(get_destination: Callable[[], str], trigger:
  ScanTrigger) -> type[BaseHTTPRequestHandler]` (new signature — was
  `(get_profiles: Callable[[], list[Profile]], trigger: ScanTrigger)`),
  `start_bridge_server(get_destination: Callable[[], str], trigger:
  ScanTrigger, port: int | None = None) -> ThreadingHTTPServer` (new
  signature — was `get_profiles` in that first position). Task 2's
  `app.py` changes consume these new signatures directly.

- [ ] **Step 1: Replace `src/scanix500/menubar/bridge.py` with this full content**

```python
from __future__ import annotations

import base64
import json
import os
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult

DEFAULT_PORT = 8765


class ScanBusyError(Exception):
    """Raised by a ScanTrigger.trigger() call when a scan is already
    running -- maps to an HTTP 409 in route_scan_request, never to a
    queued/blocked request."""


class ScanTrigger(Protocol):
    """Something that can run a named profile's scan and block until it
    completes (or raise ScanBusyError), returning the real ScanResult --
    not just "started". Implemented by ScanixMenuBarApp (see app.py) for
    the real app; FakeScanTrigger (tests/menubar/test_bridge.py) stands in
    for tests. Deliberately has no rumps/AppKit/threading dependency in
    this file -- that all lives in app.py's implementation."""

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
    get_destination: Callable[[], str], trigger: ScanTrigger
) -> type[BaseHTTPRequestHandler]:
    """Builds a BaseHTTPRequestHandler bound to a live destination-folder
    getter (a zero-arg callable, not a static string -- the "Set Bridge
    Scan Folder..." setting can change via the menu while the bridge is
    running, and every request must see the current value) and a
    ScanTrigger."""

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
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            # A separate, deliberately trivial route from do_POST's scan
            # trigger -- this must never touch trigger/self._scanning, so a
            # caller (Dossiary) can probe "is the bridge here" without any
            # risk of firing a real scan at an unknown port.
            path = urlsplit(self.path).path
            if path == "/health":
                self._send_json(200, {"service": "scanix500-bridge"})
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            # Never reads self.rfile -- safe only because protocol_version
            # stays the default HTTP/1.0 (each connection closes after one
            # response, so there's no leftover unread body to corrupt a
            # later request on the same connection); revisit if this is
            # ever bumped to HTTP/1.1 with request bodies in play. The
            # scan's own parameters live entirely in the query string, not
            # the body, so this constraint is unaffected by the
            # parameterized-scan change.
            path = urlsplit(self.path).path
            if path != "/scan":
                self._send_json(404, {"error": "not found"})
                return
            query = urlsplit(self.path).query
            params = {key: values[0] for key, values in parse_qs(query).items()}
            status, body = route_scan_request(get_destination(), params, trigger)
            self._send_json(status, body)

        def log_message(self, format: str, *args: object) -> None:
            # scanix500-menubar has no console under launchd -- keep quiet
            # rather than writing to a stderr nothing reads.
            pass

    return Handler


def start_bridge_server(
    get_destination: Callable[[], str], trigger: ScanTrigger, port: int | None = None
) -> ThreadingHTTPServer:
    """Starts the bridge listening on 127.0.0.1:<port> in a daemon thread
    and returns the live server. port resolution order: this parameter (if
    given) > SCANIX500_BRIDGE_PORT env var > DEFAULT_PORT."""
    resolved_port = port if port is not None else int(os.environ.get("SCANIX500_BRIDGE_PORT", DEFAULT_PORT))
    handler_cls = make_handler_class(get_destination, trigger)
    server = ThreadingHTTPServer(("127.0.0.1", resolved_port), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
```

- [ ] **Step 2: Replace `tests/menubar/test_bridge.py` with this full content**

```python
import base64
import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from scanix500.menubar.bridge import ScanBusyError, route_scan_request, start_bridge_server
from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult


@pytest.fixture
def unused_tcp_port() -> int:
    """A real free TCP port on 127.0.0.1, found by binding to port 0 and
    reading back what the OS assigned -- avoids hardcoding a port number
    that a previous test run's server might still be shutting down on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="POST", data=b"")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# The three boolean query params every /scan request needs -- a plain
# "everything off" baseline most tests start from and override pieces of.
DEFAULT_PARAMS = {"skip_blank_filter": "false", "skip_ocr": "false", "split_on_blank": "false"}


def _scan_url(base: str, **overrides: str) -> str:
    params = {**DEFAULT_PARAMS, **overrides}
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}/scan?{query}"


class FakeScanTrigger:
    """Test double for bridge.ScanTrigger -- records the profile it was
    called with and returns/raises whatever the test configures, with no
    real threading, rumps, or AppKit involved."""

    def __init__(self, result: ScanResult | None = None, raises: Exception | None = None):
        self.result = result
        self.raises = raises
        self.called_with: Profile | None = None

    def trigger(self, profile: Profile) -> ScanResult:
        self.called_with = profile
        if self.raises is not None:
            raise self.raises
        return self.result


def test_route_scan_request_missing_param_returns_400():
    trigger = FakeScanTrigger()

    status, body = route_scan_request("/tmp/scans", {"skip_blank_filter": "false", "skip_ocr": "false"}, trigger)

    assert status == 400
    assert "split_on_blank" in body["error"]
    assert trigger.called_with is None


def test_route_scan_request_invalid_param_value_returns_400():
    trigger = FakeScanTrigger()

    status, body = route_scan_request(
        "/tmp/scans",
        {"skip_blank_filter": "yes", "skip_ocr": "false", "split_on_blank": "false"},
        trigger,
    )

    assert status == 400
    assert "skip_blank_filter" in body["error"]
    assert trigger.called_with is None


def test_route_scan_request_busy_returns_409():
    trigger = FakeScanTrigger(raises=ScanBusyError())

    status, body = route_scan_request("/tmp/scans", DEFAULT_PARAMS, trigger)

    assert status == 409
    assert "already in progress" in body["error"]


def test_route_scan_request_success_returns_200_with_scan_result_shape(tmp_path):
    scan_file = tmp_path / "scan_1.pdf"
    scan_file.write_bytes(b"%PDF-1.4 fake pdf bytes")
    result = ScanResult(ok=True, partial=False, message=str(scan_file), output_paths=[str(scan_file)])
    trigger = FakeScanTrigger(result=result)

    status, body = route_scan_request(str(tmp_path), DEFAULT_PARAMS, trigger)

    assert status == 200
    assert body == {
        "ok": True,
        "partial": False,
        "message": str(scan_file),
        "output_paths": [str(scan_file)],
        "files": [
            {
                "filename": "scan_1.pdf",
                "content_base64": base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode("ascii"),
            }
        ],
    }
    assert trigger.called_with == Profile(
        name="Dossiary Bridge Scan", destination=str(tmp_path),
        skip_blank_filter=False, skip_ocr=False, split_on_blank=False,
    )


def test_route_scan_request_builds_profile_from_params_not_a_lookup(tmp_path):
    # The whole point of this change: the ephemeral Profile's flags come
    # directly from the request, not from any stored profiles.json entry.
    trigger = FakeScanTrigger(result=ScanResult(True, False, "ok", []))

    route_scan_request(
        str(tmp_path),
        {"skip_blank_filter": "true", "skip_ocr": "true", "split_on_blank": "true"},
        trigger,
    )

    assert trigger.called_with == Profile(
        name="Dossiary Bridge Scan", destination=str(tmp_path),
        skip_blank_filter=True, skip_ocr=True, split_on_blank=True,
    )


def test_route_scan_request_unreadable_output_path_is_skipped_in_files():
    # A path in output_paths that can no longer be read (removed between
    # the scan finishing and this call, permissions, etc.) must not raise
    # -- it's just omitted from `files`. output_paths itself is
    # untouched, and the safety-net copy in the destination folder
    # remains the record of truth regardless.
    result = ScanResult(ok=True, partial=False, message="ok", output_paths=["/tmp/scans/does-not-exist.pdf"])
    trigger = FakeScanTrigger(result=result)

    status, body = route_scan_request("/tmp/scans", DEFAULT_PARAMS, trigger)

    assert status == 200
    assert body["output_paths"] == ["/tmp/scans/does-not-exist.pdf"]
    assert body["files"] == []


def test_route_scan_request_multi_file_result_encodes_every_file(tmp_path):
    scan_1 = tmp_path / "scan_1.pdf"
    scan_2 = tmp_path / "scan_2.pdf"
    scan_1.write_bytes(b"first document")
    scan_2.write_bytes(b"second document")
    result = ScanResult(ok=True, partial=False, message="2 documents", output_paths=[str(scan_1), str(scan_2)])
    trigger = FakeScanTrigger(result=result)

    status, body = route_scan_request(str(tmp_path), {**DEFAULT_PARAMS, "split_on_blank": "true"}, trigger)

    assert status == 200
    assert body["files"] == [
        {"filename": "scan_1.pdf", "content_base64": base64.b64encode(b"first document").decode("ascii")},
        {"filename": "scan_2.pdf", "content_base64": base64.b64encode(b"second document").decode("ascii")},
    ]


def test_route_scan_request_partial_result_still_returns_200():
    result = ScanResult(
        ok=False,
        partial=True,
        message="Multi-feed detected at sheet 3 — partial scan saved to /tmp/scans/scan_1.pdf",
        output_paths=["/tmp/scans/scan_1.pdf"],
    )
    trigger = FakeScanTrigger(result=result)

    status, body = route_scan_request("/tmp/scans", {**DEFAULT_PARAMS, "split_on_blank": "true"}, trigger)

    assert status == 200
    assert body["ok"] is False
    assert body["partial"] is True


def test_bridge_server_round_trip_delivers_file_bytes(tmp_path, unused_tcp_port):
    scan_file = tmp_path / "scan_1.pdf"
    scan_file.write_bytes(b"%PDF-1.4 fake pdf bytes")
    result = ScanResult(ok=True, partial=False, message=str(scan_file), output_paths=[str(scan_file)])
    trigger = FakeScanTrigger(result=result)
    server = start_bridge_server(lambda: str(tmp_path), trigger, port=unused_tcp_port)
    try:
        status, body = _post(_scan_url(f"http://127.0.0.1:{unused_tcp_port}"))
    finally:
        server.shutdown()

    assert status == 200
    assert body["files"] == [
        {
            "filename": "scan_1.pdf",
            "content_base64": base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode("ascii"),
        }
    ]


def test_bridge_server_round_trip_success(unused_tcp_port):
    result = ScanResult(ok=True, partial=False, message="/tmp/scans/scan_1.pdf", output_paths=["/tmp/scans/scan_1.pdf"])
    trigger = FakeScanTrigger(result=result)
    server = start_bridge_server(lambda: "/tmp/scans", trigger, port=unused_tcp_port)
    try:
        status, body = _post(_scan_url(f"http://127.0.0.1:{unused_tcp_port}"))
    finally:
        server.shutdown()

    assert status == 200
    assert body["ok"] is True
    assert trigger.called_with == Profile(
        name="Dossiary Bridge Scan", destination="/tmp/scans",
        skip_blank_filter=False, skip_ocr=False, split_on_blank=False,
    )


def test_bridge_server_round_trip_missing_params_is_400(unused_tcp_port):
    server = start_bridge_server(lambda: "/tmp/scans", FakeScanTrigger(), port=unused_tcp_port)
    try:
        status, body = _post(f"http://127.0.0.1:{unused_tcp_port}/scan")
    finally:
        server.shutdown()

    assert status == 400
    assert "skip_blank_filter" in body["error"]


def test_bridge_server_round_trip_busy_is_409(unused_tcp_port):
    trigger = FakeScanTrigger(raises=ScanBusyError())
    server = start_bridge_server(lambda: "/tmp/scans", trigger, port=unused_tcp_port)
    try:
        status, _ = _post(_scan_url(f"http://127.0.0.1:{unused_tcp_port}"))
    finally:
        server.shutdown()

    assert status == 409


def test_bridge_server_response_has_cors_header(unused_tcp_port):
    result = ScanResult(ok=True, partial=False, message="ok", output_paths=[])
    server = start_bridge_server(lambda: "/tmp/scans", FakeScanTrigger(result=result), port=unused_tcp_port)
    try:
        req = urllib.request.Request(_scan_url(f"http://127.0.0.1:{unused_tcp_port}"), method="POST", data=b"")
        with urllib.request.urlopen(req) as resp:
            assert resp.headers["Access-Control-Allow-Origin"] == "*"
    finally:
        server.shutdown()


def test_bridge_server_options_preflight_is_handled(unused_tcp_port):
    server = start_bridge_server(lambda: "/tmp/scans", FakeScanTrigger(), port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/scan", method="OPTIONS")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 204
            assert resp.headers["Access-Control-Allow-Origin"] == "*"
    finally:
        server.shutdown()


def test_bridge_server_reads_destination_live_not_a_snapshot(unused_tcp_port):
    # get_destination is called fresh on every request, not captured once
    # at start_bridge_server() time -- proves a destination changed via
    # the new "Set Bridge Scan Folder..." menu item while the bridge is
    # already running is picked up immediately.
    destination_box = ["/tmp/scans-old"]
    trigger = FakeScanTrigger(result=ScanResult(True, False, "ok", []))
    server = start_bridge_server(lambda: destination_box[0], trigger, port=unused_tcp_port)
    try:
        _post(_scan_url(f"http://127.0.0.1:{unused_tcp_port}"))
        assert trigger.called_with.destination == "/tmp/scans-old"
        destination_box[0] = "/tmp/scans-new"
        _post(_scan_url(f"http://127.0.0.1:{unused_tcp_port}"))
        assert trigger.called_with.destination == "/tmp/scans-new"
    finally:
        server.shutdown()


def test_bridge_server_health_endpoint_returns_200(unused_tcp_port):
    trigger = FakeScanTrigger()
    server = start_bridge_server(lambda: "/tmp/scans", trigger, port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/health", method="GET")
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            body = json.loads(resp.read())
            headers = resp.headers
    finally:
        server.shutdown()

    assert status == 200
    assert body == {"service": "scanix500-bridge"}
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert trigger.called_with is None


def test_bridge_server_get_unknown_path_is_404(unused_tcp_port):
    server = start_bridge_server(lambda: "/tmp/scans", FakeScanTrigger(), port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/scan", method="GET")
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            status = e.code
            body = json.loads(e.read())
    finally:
        server.shutdown()

    assert status == 404
    assert body == {"error": "not found"}


def test_bridge_server_options_preflight_advertises_get(unused_tcp_port):
    server = start_bridge_server(lambda: "/tmp/scans", FakeScanTrigger(), port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/health", method="OPTIONS")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 204
            assert "GET" in resp.headers["Access-Control-Allow-Methods"]
    finally:
        server.shutdown()


def test_bridge_server_unhandled_method_still_has_cors_header(unused_tcp_port):
    # GET is explicitly handled by do_GET (see /health and its 404
    # fallback above) -- use PUT here instead, a method this bridge
    # genuinely never implements, to keep testing
    # BaseHTTPRequestHandler's own unhandled-method fallback (the
    # send_error override above), not do_GET's own routing.
    server = start_bridge_server(lambda: "/tmp/scans", FakeScanTrigger(), port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/scan", method="PUT")
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
                headers = resp.headers
        except urllib.error.HTTPError as e:
            status = e.code
            headers = e.headers
    finally:
        server.shutdown()

    assert status == 501
    assert headers["Access-Control-Allow-Origin"] == "*"
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/menubar/test_bridge.py -v`
Expected: all tests PASS.

- [ ] **Step 4: Run the full test suite**

Run: `.venv/bin/pytest -q`
Expected: all tests PASS — this task doesn't touch `app.py`, `profiles.py`,
or `runner.py`, so `test_app.py`/`test_profiles.py`/`test_runner.py`
should be unaffected.

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/menubar/bridge.py tests/menubar/test_bridge.py
git commit -m "feat: parameterize the scan bridge, remove profile lookup"
```

---

### Task 2: bridge scan folder setting + `app.py` wiring

**Files:**
- Create: `src/scanix500/menubar/bridge_settings.py`
- Create: `tests/menubar/test_bridge_settings.py`
- Modify: `src/scanix500/menubar/profile_form.py:25-38` (rename `_pick_folder` to public `pick_folder`)
- Modify: `src/scanix500/menubar/app.py`
- Modify: `tests/menubar/test_app.py`

**Interfaces:**
- Consumes: `route_scan_request`/`start_bridge_server`'s new
  `get_destination`-based signatures from Task 1.
- Produces: `bridge_settings.default_bridge_destination_path() -> Path`,
  `bridge_settings.load_bridge_destination(path: Path) -> str`,
  `bridge_settings.save_bridge_destination(path: Path, destination: str)
  -> None`, `profile_form.pick_folder(default_path: str) -> str | None`
  (renamed from `_pick_folder`, same signature and behavior).

- [ ] **Step 1: Write the failing tests for `bridge_settings.py`**

Create `tests/menubar/test_bridge_settings.py`:

```python
from pathlib import Path

from scanix500.menubar.bridge_settings import (
    default_bridge_destination_path,
    load_bridge_destination,
    save_bridge_destination,
)


def test_load_returns_documents_scans_default_when_file_missing(tmp_path):
    path = tmp_path / "does-not-exist" / "bridge_destination.txt"

    loaded = load_bridge_destination(path)

    assert loaded == str(Path.home() / "Documents" / "Scans")


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "bridge_destination.txt"

    save_bridge_destination(path, "/tmp/dossiary-scans")
    loaded = load_bridge_destination(path)

    assert loaded == "/tmp/dossiary-scans"


def test_load_falls_back_to_default_for_empty_file(tmp_path):
    path = tmp_path / "bridge_destination.txt"
    path.write_text("   \n")

    loaded = load_bridge_destination(path)

    assert loaded == str(Path.home() / "Documents" / "Scans")


def test_default_bridge_destination_path_lives_next_to_profiles_json():
    path = default_bridge_destination_path()

    assert path.parent == Path.home() / "Library" / "Application Support" / "scanix500"
    assert path.name == "bridge_destination.txt"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/menubar/test_bridge_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named
'scanix500.menubar.bridge_settings'`.

- [ ] **Step 3: Create `src/scanix500/menubar/bridge_settings.py`**

```python
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_DEFAULT_DESTINATION = str(Path.home() / "Documents" / "Scans")


def default_bridge_destination_path() -> Path:
    return Path.home() / "Library" / "Application Support" / "scanix500" / "bridge_destination.txt"


def load_bridge_destination(path: Path) -> str:
    """The folder a Dossiary-triggered (bridge) scan's safety-net copy is
    written to -- a single small value, not a Profile, since there's no
    more named-profile concept on this path (see the 2026-09-16
    parameterized-scan design spec). Falls back to the same
    ~/Documents/Scans default the "Default"/"Hardware Button" profiles
    already use, both when the file doesn't exist yet and when it exists
    but is empty/whitespace-only."""
    if not path.exists():
        return _DEFAULT_DESTINATION
    value = path.read_text().strip()
    return value or _DEFAULT_DESTINATION


def save_bridge_destination(path: Path, destination: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically so an interrupted write can't leave a truncated
    # file for the next load to trip over -- same pattern
    # profiles.save_profiles() already uses.
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(destination)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/menubar/test_bridge_settings.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Rename `_pick_folder` to public `pick_folder` in `profile_form.py`**

In `src/scanix500/menubar/profile_form.py`, this function (currently at
lines 25-38):

```python
def _pick_folder(default_path: str) -> str | None:
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseDirectories_(True)
    panel.setCanChooseFiles_(False)
    panel.setCanCreateDirectories_(True)
    panel.setAllowsMultipleSelection_(False)
    panel.setDirectoryURL_(NSURL.fileURLWithPath_(default_path))
    # 1 is NSOpenPanel's documented "OK button" response — the same
    # convention app.py's own folder picker already relied on (confirmed
    # against the installed rumps/PyObjC source during Phase 2's final
    # review).
    if panel.runModal() == 1:
        return str(panel.URL().path())
    return None
```

Rename it to a public name (it's now used from `app.py` too, for the new
"Set Bridge Scan Folder…" menu item, not just from inside this module):

```python
def pick_folder(default_path: str) -> str | None:
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseDirectories_(True)
    panel.setCanChooseFiles_(False)
    panel.setCanCreateDirectories_(True)
    panel.setAllowsMultipleSelection_(False)
    panel.setDirectoryURL_(NSURL.fileURLWithPath_(default_path))
    # 1 is NSOpenPanel's documented "OK button" response — the same
    # convention app.py's own folder picker already relied on (confirmed
    # against the installed rumps/PyObjC source during Phase 2's final
    # review).
    if panel.runModal() == 1:
        return str(panel.URL().path())
    return None
```

Then find the one call site inside this same file, `chooseClicked_`
(currently):

```python
    def chooseClicked_(self, _sender) -> None:
        chosen = _pick_folder(self.destination)
        if chosen is not None:
            self.destination = chosen
            self.destination_field.setStringValue_(chosen)
```

Update it to call the renamed function:

```python
    def chooseClicked_(self, _sender) -> None:
        chosen = pick_folder(self.destination)
        if chosen is not None:
            self.destination = chosen
            self.destination_field.setStringValue_(chosen)
```

- [ ] **Step 6: Wire `app.py`**

In `src/scanix500/menubar/app.py`, update the imports. Find:

```python
from scanix500.menubar.bridge import ScanBusyError, start_bridge_server
from scanix500.menubar.button_watcher import button_pressed, resolve_device_name
from scanix500.menubar.profile_form import show_profile_form
from scanix500.menubar.profiles import (
    HARDWARE_BUTTON_PROFILE_NAME,
    Profile,
    add_profile,
    default_profiles_path,
    delete_profile,
    find_profile,
    load_profiles,
    replace_profile,
    save_profiles,
)
```

Replace with:

```python
from scanix500.menubar.bridge import ScanBusyError, start_bridge_server
from scanix500.menubar.bridge_settings import (
    default_bridge_destination_path,
    load_bridge_destination,
    save_bridge_destination,
)
from scanix500.menubar.button_watcher import button_pressed, resolve_device_name
from scanix500.menubar.profile_form import pick_folder, show_profile_form
from scanix500.menubar.profiles import (
    HARDWARE_BUTTON_PROFILE_NAME,
    Profile,
    add_profile,
    default_profiles_path,
    delete_profile,
    find_profile,
    load_profiles,
    replace_profile,
    save_profiles,
)
```

Find `__init__`'s current body:

```python
    def __init__(self):
        super().__init__("scanix500", title=IDLE_TITLE)
        self.profiles_path = default_profiles_path()
        self.profiles = load_profiles(self.profiles_path)
        self._scanning = False
        # See _rebuild_menu's comment on self._app_started.
        self._app_started = False
        self._rebuild_menu()
        # Resolved once and cached indefinitely — confirmed empirically (unplug/
        # replug test against real hardware, 2026-09-14) that this backend's
        # device name is based on the unit's persistent serial number, not a
        # USB bus path, so it stays stable across reconnects. No invalidation
        # needed.
        self._button_device_name: str | None = None
        self._ticks_since_resolve_attempt = 0
        self._button_timer = rumps.Timer(self._poll_button, 1.5)
        self._button_timer.start()
        # Embedded HTTP bridge (see bridge.py) -- lets an external caller
        # (Dossiary's browser JS) trigger a scan by profile name over
        # local HTTP. self.profiles is read fresh on every bridge request
        # (via the lambda), not snapshotted here, so Add/Edit/Delete
        # Profile while the bridge is running is picked up immediately.
        # The bridge is an optional enhancement, not core functionality --
        # a taken port (OSError) or a non-numeric SCANIX500_BRIDGE_PORT
        # (ValueError) must never take the whole menu bar app down with it,
        # since a launchd-launched process has nowhere useful for an
        # uncaught traceback to go.
        try:
            self._bridge_server = start_bridge_server(lambda: self.profiles, self)
        except (OSError, ValueError) as e:
            self._bridge_server = None
            rumps.notification(title="Scan bridge unavailable", subtitle="", message=str(e))
```

Replace with:

```python
    def __init__(self):
        super().__init__("scanix500", title=IDLE_TITLE)
        self.profiles_path = default_profiles_path()
        self.profiles = load_profiles(self.profiles_path)
        self.bridge_destination_path = default_bridge_destination_path()
        self.bridge_destination = load_bridge_destination(self.bridge_destination_path)
        self._scanning = False
        # See _rebuild_menu's comment on self._app_started.
        self._app_started = False
        self._rebuild_menu()
        # Resolved once and cached indefinitely — confirmed empirically (unplug/
        # replug test against real hardware, 2026-09-14) that this backend's
        # device name is based on the unit's persistent serial number, not a
        # USB bus path, so it stays stable across reconnects. No invalidation
        # needed.
        self._button_device_name: str | None = None
        self._ticks_since_resolve_attempt = 0
        self._button_timer = rumps.Timer(self._poll_button, 1.5)
        self._button_timer.start()
        # Embedded HTTP bridge (see bridge.py) -- lets an external caller
        # (Dossiary's browser JS) trigger a scan with its own parameters
        # over local HTTP. self.bridge_destination is read fresh on every
        # bridge request (via the lambda), not snapshotted here, so
        # changing "Set Bridge Scan Folder…" while the bridge is running
        # is picked up immediately. The bridge is an optional enhancement,
        # not core functionality -- a taken port (OSError) or a
        # non-numeric SCANIX500_BRIDGE_PORT (ValueError) must never take
        # the whole menu bar app down with it, since a launchd-launched
        # process has nowhere useful for an uncaught traceback to go.
        try:
            self._bridge_server = start_bridge_server(lambda: self.bridge_destination, self)
        except (OSError, ValueError) as e:
            self._bridge_server = None
            rumps.notification(title="Scan bridge unavailable", subtitle="", message=str(e))
```

Find `_rebuild_menu()`'s current body (the profile-list/Add/Edit/Delete
section, before the Quit-item comment block):

```python
    def _rebuild_menu(self):
        self.menu.clear()
        for profile in self.profiles:
            self.menu.add(rumps.MenuItem(profile.name, callback=self._make_scan_handler(profile)))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Add Profile…", callback=self._add_profile))

        edit_menu = rumps.MenuItem("Edit Profile…")
        for profile in self.profiles:
            edit_menu.add(rumps.MenuItem(profile.name, callback=self._make_edit_handler(profile.name)))
        self.menu.add(edit_menu)

        delete_menu = rumps.MenuItem("Delete Profile…")
        for profile in self.profiles:
            delete_menu.add(rumps.MenuItem(profile.name, callback=self._make_delete_handler(profile.name)))
        self.menu.add(delete_menu)
```

Add the new menu item right after the `delete_menu` line, before the
Quit-item comment block:

```python
    def _rebuild_menu(self):
        self.menu.clear()
        for profile in self.profiles:
            self.menu.add(rumps.MenuItem(profile.name, callback=self._make_scan_handler(profile)))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Add Profile…", callback=self._add_profile))

        edit_menu = rumps.MenuItem("Edit Profile…")
        for profile in self.profiles:
            edit_menu.add(rumps.MenuItem(profile.name, callback=self._make_edit_handler(profile.name)))
        self.menu.add(edit_menu)

        delete_menu = rumps.MenuItem("Delete Profile…")
        for profile in self.profiles:
            delete_menu.add(rumps.MenuItem(profile.name, callback=self._make_delete_handler(profile.name)))
        self.menu.add(delete_menu)

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Set Bridge Scan Folder…", callback=self._set_bridge_destination))
```

(The Quit-item comment block and its `if self._app_started and
self.quit_button is not None:` logic immediately below stay exactly
where they are, unchanged — this new item is added before that block,
same as every other menu item above it.)

Add the new handler method. Find `_add_profile`'s current definition
(the first method after `_on_scan_complete`):

```python
    def _add_profile(self, _sender):
```

Add the new handler immediately before it:

```python
    def _set_bridge_destination(self, _sender):
        if self._scanning:
            return
        chosen = pick_folder(self.bridge_destination)
        if chosen is None:
            return
        self.bridge_destination = chosen
        save_bridge_destination(self.bridge_destination_path, chosen)

    def _add_profile(self, _sender):
```

- [ ] **Step 7: Update `tests/menubar/test_app.py`'s two `__init__` tests**

The two existing `test_init_survives_bridge_startup_*` tests construct a
real `ScanixMenuBarApp()`, which now also calls
`default_bridge_destination_path()`/`load_bridge_destination()` during
`__init__` — both need patching too, or these tests will try to touch the
real filesystem. Find:

```python
def test_init_survives_bridge_startup_oserror():
    # The bridge is an optional enhancement -- a taken port (OSError, e.g.
    # another scanix500-menubar already running) must not take the whole
    # menu bar app down with it. __init__ must complete, self._bridge_server
    # must be set to None (not left unset, and not the OSError), and the
    # failure must be surfaced via a notification rather than silently
    # swallowed.
    from scanix500.menubar.app import ScanixMenuBarApp

    with (
        patch("scanix500.menubar.app.load_profiles", return_value=[Profile(name="Default", destination="/tmp/scans")]),
        patch("scanix500.menubar.app.default_profiles_path", return_value="/tmp/scanix500-test-profiles.json"),
        patch("scanix500.menubar.app.start_bridge_server", side_effect=OSError("Address already in use")),
        patch("scanix500.menubar.app.rumps.notification") as notification,
    ):
        app = ScanixMenuBarApp()

    assert app._bridge_server is None
    assert notification.called
    assert notification.call_args.kwargs["title"] == "Scan bridge unavailable"


def test_init_survives_bridge_startup_valueerror():
    # Same as above, but for a non-numeric SCANIX500_BRIDGE_PORT (which
    # start_bridge_server's own int(os.environ.get(...)) raises ValueError
    # on) rather than a taken port.
    from scanix500.menubar.app import ScanixMenuBarApp

    with (
        patch("scanix500.menubar.app.load_profiles", return_value=[Profile(name="Default", destination="/tmp/scans")]),
        patch("scanix500.menubar.app.default_profiles_path", return_value="/tmp/scanix500-test-profiles.json"),
        patch("scanix500.menubar.app.start_bridge_server", side_effect=ValueError("invalid literal for int()")),
        patch("scanix500.menubar.app.rumps.notification") as notification,
    ):
        app = ScanixMenuBarApp()

    assert app._bridge_server is None
    assert notification.called
```

Replace both `with (...)` blocks' patch lists to add the two new patches
(add these two lines to each of the two `with` blocks, right after the
`default_profiles_path` patch line):

```python
        patch("scanix500.menubar.app.load_bridge_destination", return_value="/tmp/scans"),
        patch("scanix500.menubar.app.default_bridge_destination_path", return_value="/tmp/scanix500-test-bridge-destination.txt"),
```

So the full `oserror` test becomes:

```python
def test_init_survives_bridge_startup_oserror():
    # The bridge is an optional enhancement -- a taken port (OSError, e.g.
    # another scanix500-menubar already running) must not take the whole
    # menu bar app down with it. __init__ must complete, self._bridge_server
    # must be set to None (not left unset, and not the OSError), and the
    # failure must be surfaced via a notification rather than silently
    # swallowed.
    from scanix500.menubar.app import ScanixMenuBarApp

    with (
        patch("scanix500.menubar.app.load_profiles", return_value=[Profile(name="Default", destination="/tmp/scans")]),
        patch("scanix500.menubar.app.default_profiles_path", return_value="/tmp/scanix500-test-profiles.json"),
        patch("scanix500.menubar.app.load_bridge_destination", return_value="/tmp/scans"),
        patch("scanix500.menubar.app.default_bridge_destination_path", return_value="/tmp/scanix500-test-bridge-destination.txt"),
        patch("scanix500.menubar.app.start_bridge_server", side_effect=OSError("Address already in use")),
        patch("scanix500.menubar.app.rumps.notification") as notification,
    ):
        app = ScanixMenuBarApp()

    assert app._bridge_server is None
    assert notification.called
    assert notification.call_args.kwargs["title"] == "Scan bridge unavailable"
```

and the `valueerror` test becomes:

```python
def test_init_survives_bridge_startup_valueerror():
    # Same as above, but for a non-numeric SCANIX500_BRIDGE_PORT (which
    # start_bridge_server's own int(os.environ.get(...)) raises ValueError
    # on) rather than a taken port.
    from scanix500.menubar.app import ScanixMenuBarApp

    with (
        patch("scanix500.menubar.app.load_profiles", return_value=[Profile(name="Default", destination="/tmp/scans")]),
        patch("scanix500.menubar.app.default_profiles_path", return_value="/tmp/scanix500-test-profiles.json"),
        patch("scanix500.menubar.app.load_bridge_destination", return_value="/tmp/scans"),
        patch("scanix500.menubar.app.default_bridge_destination_path", return_value="/tmp/scanix500-test-bridge-destination.txt"),
        patch("scanix500.menubar.app.start_bridge_server", side_effect=ValueError("invalid literal for int()")),
        patch("scanix500.menubar.app.rumps.notification") as notification,
    ):
        app = ScanixMenuBarApp()

    assert app._bridge_server is None
    assert notification.called
```

- [ ] **Step 8: Add a test for `_set_bridge_destination`**

Append to `tests/menubar/test_app.py`:

```python
def test_set_bridge_destination_saves_chosen_folder():
    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)  # skip rumps.App.__init__/AppKit setup
    app._scanning = False
    app.bridge_destination = "/tmp/old-scans"
    app.bridge_destination_path = "/tmp/scanix500-test-bridge-destination.txt"

    with (
        patch("scanix500.menubar.app.pick_folder", return_value="/tmp/new-scans") as pick,
        patch("scanix500.menubar.app.save_bridge_destination") as save,
    ):
        app._set_bridge_destination(None)

    pick.assert_called_once_with("/tmp/old-scans")
    save.assert_called_once_with("/tmp/scanix500-test-bridge-destination.txt", "/tmp/new-scans")
    assert app.bridge_destination == "/tmp/new-scans"


def test_set_bridge_destination_does_nothing_on_cancel():
    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)
    app._scanning = False
    app.bridge_destination = "/tmp/old-scans"
    app.bridge_destination_path = "/tmp/scanix500-test-bridge-destination.txt"

    with (
        patch("scanix500.menubar.app.pick_folder", return_value=None),
        patch("scanix500.menubar.app.save_bridge_destination") as save,
    ):
        app._set_bridge_destination(None)

    save.assert_not_called()
    assert app.bridge_destination == "/tmp/old-scans"


def test_set_bridge_destination_does_nothing_while_scanning():
    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)
    app._scanning = True
    app.bridge_destination = "/tmp/old-scans"

    with patch("scanix500.menubar.app.pick_folder") as pick:
        app._set_bridge_destination(None)

    pick.assert_not_called()
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/menubar/test_bridge_settings.py tests/menubar/test_app.py -v`
Expected: all tests PASS.

- [ ] **Step 10: Run the full test suite**

Run: `.venv/bin/pytest -q`
Expected: all tests PASS.

- [ ] **Step 11: Commit**

```bash
git add src/scanix500/menubar/bridge_settings.py src/scanix500/menubar/profile_form.py src/scanix500/menubar/app.py tests/menubar/test_bridge_settings.py tests/menubar/test_app.py
git commit -m "feat: add bridge scan folder setting, wire into app.py menu"
```

---

### Task 3: Documentation — README.md and CLAUDE.md

**Files:**
- Modify: `README.md` (Phase 4's "Use" section, its manual checklist, and the "Dossiary integration" section)
- Modify: `CLAUDE.md` (the HTTP bridge bullet)

**Interfaces:**
- Consumes: the parameterized `/scan` endpoint and the new bridge-scan-folder
  setting from Tasks 1-2 (documentation only — no code in this task).
- Produces: nothing consumed by later tasks; this is the plan's last task.

- [ ] **Step 1: Update Phase 4's "Use" section in README.md**

Replace this paragraph and its JSON example:

```markdown
With the menu bar app running, `POST /scan/<profile-name>` (the profile
name is the final URL path segment, percent-encoded) to
`http://127.0.0.1:8765` triggers a scan using that profile, exactly as if
you'd clicked it in the menu — and blocks until the scan finishes,
returning a JSON body:

```json
{"ok": true, "partial": false, "message": "/path/to/scan.pdf", "output_paths": ["/path/to/scan.pdf"], "files": [{"filename": "scan.pdf", "content_base64": "JVBERi0xLjQK..."}]}
```

`ok: false` with `partial: true` means a partial scan (e.g. a multi-feed
jam) still produced a usable file, named in `output_paths`. `ok: false`
with `partial: false` means the scan failed outright. A `404` means no
profile by that name exists; a `409` means a scan is already in progress
(from any trigger — menu, hardware button, or another bridge request) and
this request was rejected immediately, not queued.
```

with:

```markdown
With the menu bar app running, `POST /scan?skip_blank_filter=<true|false>&skip_ocr=<true|false>&split_on_blank=<true|false>`
to `http://127.0.0.1:8765` triggers a scan using exactly those settings —
no profile involved — and blocks until the scan finishes, returning a
JSON body:

```json
{"ok": true, "partial": false, "message": "/path/to/scan.pdf", "output_paths": ["/path/to/scan.pdf"], "files": [{"filename": "scan.pdf", "content_base64": "JVBERi0xLjQK..."}]}
```

All three query parameters are required and must be exactly `true` or
`false`; a missing or malformed one is a `400` naming which parameter(s)
were the problem. `ok: false` with `partial: true` means a partial scan
(e.g. a multi-feed jam) still produced a usable file, named in
`output_paths`. `ok: false` with `partial: false` means the scan failed
outright. A `409` means a scan is already in progress (from any trigger —
menu, hardware button, or another bridge request) and this request was
rejected immediately, not queued.

Every bridge-triggered scan writes its safety-net copy to one shared
folder, not a per-request destination — see **"Set Bridge Scan
Folder…"** below.
```

- [ ] **Step 2: Update the smoke-test command**

Replace:

```markdown
**Smoke test** (with a profile named `"Default"` already configured and
paper loaded in the ADF):

    curl -i -X POST http://127.0.0.1:8765/scan/Default
```

with:

```markdown
**Smoke test** (with paper loaded in the ADF):

    curl -i -X POST "http://127.0.0.1:8765/scan?skip_blank_filter=false&skip_ocr=false&split_on_blank=false"
```

- [ ] **Step 3: Add a "Set Bridge Scan Folder…" subsection**

Immediately after the `GET /health` paragraph (the one ending "...before
falling back to asking the person to confirm the port manually:" plus its
`curl -i http://127.0.0.1:8765/health` code block) and before the "Trust
model" paragraph, insert:

```markdown
**Every bridge-triggered scan's safety-net copy is written to one shared
folder**, configured via the menu bar app's own **"Set Bridge Scan
Folder…"** menu item (a native folder picker — the same one Add/Edit
Profile already uses). Defaults to `~/Documents/Scans` until you set it
explicitly. This is deliberately not a per-request destination — the
caller (Dossiary) already receives the scanned bytes directly in `files`
(see above), so this folder exists purely as a local backup, not as
something a caller picks per scan.
```

- [ ] **Step 4: Update the trust-model paragraph's URL reference**

Find, inside the existing "Trust model" paragraph:

```
so **any
web page you visit in any browser on this machine, or any other local
process**, can `POST` to `http://127.0.0.1:8765/scan/<profile>`, trigger a
real physical scan, and read back both `output_paths` (absolute
```

Replace just the URL fragment:

```
so **any
web page you visit in any browser on this machine, or any other local
process**, can `POST` to `http://127.0.0.1:8765/scan`, trigger a
real physical scan, and read back both `output_paths` (absolute
```

(Only `/scan/<profile>` → `/scan` changes; the rest of the sentence and
paragraph is unchanged.)

- [ ] **Step 5: Replace the "Dossiary integration" section**

Replace the entire section:

```markdown
### Dossiary integration

[Dossiary](https://github.com/AarneAarebye/Dossiary) (a separate app) has its
own "Scan"/"Scan Multi" toolbar buttons that call this bridge. They expect
two specific, fixed profile names to already exist:

- **`Dossiary Scan`** — an ordinary profile.
- **`Dossiary Scan Multi`** — a profile with "Split on blank separator
  sheets?" answered Yes.

Create both once via **Add Profile…** in the menu bar app. **Once
Dossiary's own auto-connect support ships** (it will read the `files`
field described above and write the scan directly into whichever
library's `inbox/` is currently open), `destination` will no longer need
to point at any particular Dossiary library — it'll become a local
safety-net copy, and you'll be able to point it anywhere you like.
**Until then, keep `destination` set to your Dossiary library's actual
`inbox/` folder** (e.g. `/path/to/your/library/inbox`) — the
currently-shipped Dossiary ignores `files` entirely and still relies on
its own "Check inbox" flow finding the file already sitting there. These
names are still a fixed contract Dossiary's own code depends on;
renaming either profile breaks the integration until Dossiary's own
setting is updated to match, or the profile is renamed back.
```

with:

```markdown
### Dossiary integration

[Dossiary](https://github.com/AarneAarebye/Dossiary) (a separate app) has
its own "Scan"/"Scan Multi" toolbar buttons that call this bridge
directly with the settings each button wants — there's no profile to
create or name to keep in sync anymore. Both Dossiary's auto-connect
support (it health-probes the bridge and reads `files` to write the scan
into whichever library's `inbox/` is currently open) and this
parameterized endpoint already ship, so nothing here needs manual setup
beyond, optionally, changing **"Set Bridge Scan Folder…"** if you don't
want the safety-net copy landing in `~/Documents/Scans`.
```

- [ ] **Step 6: Update the manual UI checklist**

In the "Manual UI checklist" section, replace these items:

```markdown
- [ ] With a profile named `"Default"` already present and paper loaded
      in the ADF, `curl -i -X POST http://127.0.0.1:8765/scan/Default` —
      the menu bar icon changes to the scanning state, the request blocks
      until the scan completes, and the response is `200` with a JSON
      body naming the output PDF path.
- [ ] The same response's `files` field contains a `content_base64` entry
      for that PDF — decode it locally (e.g.
      `python3 -c "import base64,sys; open('out.pdf','wb').write(base64.b64decode(sys.stdin.read()))"`,
      piping the field's value in) and confirm it opens as a valid PDF.
- [ ] `curl -i http://127.0.0.1:8765/health` → `200` with
      `{"service": "scanix500-bridge"}`, and the menu bar icon does
      **not** change to its scanning state (proves the probe never
      touches the scanner).
- [ ] `curl -i -X POST http://127.0.0.1:8765/scan/Nonexistent` → `404`.
- [ ] While a scan is running (from the check above, or a physical button
      press), a second `curl -i -X POST http://127.0.0.1:8765/scan/Default`
      from another terminal → `409`.
- [ ] `curl -i -X OPTIONS http://127.0.0.1:8765/scan/Default` → `204`
      with `Access-Control-Allow-Origin: *`.
```

with:

```markdown
- [ ] With paper loaded in the ADF, `curl -i -X POST
      "http://127.0.0.1:8765/scan?skip_blank_filter=false&skip_ocr=false&split_on_blank=false"`
      — the menu bar icon changes to the scanning state, the request
      blocks until the scan completes, and the response is `200` with a
      JSON body naming the output PDF path.
- [ ] The same response's `files` field contains a `content_base64` entry
      for that PDF — decode it locally (e.g.
      `python3 -c "import base64,sys; open('out.pdf','wb').write(base64.b64decode(sys.stdin.read()))"`,
      piping the field's value in) and confirm it opens as a valid PDF.
- [ ] `curl -i http://127.0.0.1:8765/health` → `200` with
      `{"service": "scanix500-bridge"}`, and the menu bar icon does
      **not** change to its scanning state (proves the probe never
      touches the scanner).
- [ ] `curl -i -X POST "http://127.0.0.1:8765/scan?skip_blank_filter=false"`
      (missing `skip_ocr`/`split_on_blank`) → `400`, naming both missing
      parameters.
- [ ] While a scan is running (from the check above, or a physical button
      press), a second `curl -i -X POST
      "http://127.0.0.1:8765/scan?skip_blank_filter=false&skip_ocr=false&split_on_blank=false"`
      from another terminal → `409`.
- [ ] `curl -i -X OPTIONS http://127.0.0.1:8765/scan` → `204` with
      `Access-Control-Allow-Origin: *`.
- [ ] **"Set Bridge Scan Folder…"** appears in the menu; choosing a new
      folder and then running the smoke-test `curl` above writes the
      safety-net PDF into that new folder, not the old one — confirm by
      checking the folder's contents before and after.
```

- [ ] **Step 7: Update CLAUDE.md**

Find, inside the "Scan / Scan Multi toolbar buttons" ~~"HTTP bridge"~~
bullet's closing sentence:

```
  Fixed, two-way contract with Dossiary: profiles named exactly
  `"Dossiary Scan"`/`"Dossiary Scan Multi"` must exist for its toolbar
  buttons to work — see README.md's own "Dossiary integration" section.
```

Replace with:

```
  **Extended 2026-09-16 (parameterized scan)** — see
  `docs/superpowers/specs/2026-09-16-scan-bridge-parameterized-scan-design.md`
  in the sibling Dossiary repo, plan
  `docs/superpowers/plans/2026-09-16-scan-bridge-parameterized-scan.md`:
  `POST /scan/<profile-name>`'s lookup is gone. Dossiary now sends its
  scan settings directly as query parameters
  (`skip_blank_filter`/`skip_ocr`/`split_on_blank`, each required and
  exactly `true`/`false`), and `route_scan_request()` builds an ephemeral
  `Profile` from them rather than looking one up — no more requirement
  that `"Dossiary Scan"`/`"Dossiary Scan Multi"` profiles exist at all.
  `profiles.json`, the manual dropdown, and the reserved `"Hardware
  Button"` profile are untouched. The safety-net destination for a
  bridge-triggered scan now comes from a small, separate,
  menu-editable setting (`bridge_settings.py`), not a profile's own
  `destination` field — see README.md's own "Set Bridge Scan Folder…"
  note.
```

- [ ] **Step 8: Run the full test suite once**

Run: `.venv/bin/pytest -q`
Expected: unchanged from Task 2's Step 10 (docs-only changes in this task).

- [ ] **Step 9: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: document the parameterized scan bridge amendment"
```
