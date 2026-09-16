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
