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


def test_route_scan_request_unknown_profile_returns_404():
    profiles = [Profile(name="Default", destination="/tmp/scans")]
    trigger = FakeScanTrigger()

    status, body = route_scan_request(profiles, "Nonexistent", trigger)

    assert status == 404
    assert "Nonexistent" in body["error"]
    assert trigger.called_with is None


def test_route_scan_request_busy_returns_409():
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    trigger = FakeScanTrigger(raises=ScanBusyError())

    status, body = route_scan_request(profiles, "Dossiary Scan", trigger)

    assert status == 409
    assert "already in progress" in body["error"]


def test_route_scan_request_success_returns_200_with_scan_result_shape():
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    result = ScanResult(ok=True, partial=False, message="/tmp/scans/scan_1.pdf", output_paths=["/tmp/scans/scan_1.pdf"])
    trigger = FakeScanTrigger(result=result)

    status, body = route_scan_request(profiles, "Dossiary Scan", trigger)

    assert status == 200
    assert body == {
        "ok": True,
        "partial": False,
        "message": "/tmp/scans/scan_1.pdf",
        "output_paths": ["/tmp/scans/scan_1.pdf"],
    }
    assert trigger.called_with == profiles[0]


def test_route_scan_request_partial_result_still_returns_200():
    profiles = [Profile(name="Dossiary Scan Multi", destination="/tmp/scans", split_on_blank=True)]
    result = ScanResult(
        ok=False,
        partial=True,
        message="Multi-feed detected at sheet 3 — partial scan saved to /tmp/scans/scan_1.pdf",
        output_paths=["/tmp/scans/scan_1.pdf"],
    )
    trigger = FakeScanTrigger(result=result)

    status, body = route_scan_request(profiles, "Dossiary Scan Multi", trigger)

    assert status == 200
    assert body["ok"] is False
    assert body["partial"] is True


def test_bridge_server_round_trip_success(unused_tcp_port):
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    result = ScanResult(ok=True, partial=False, message="/tmp/scans/scan_1.pdf", output_paths=["/tmp/scans/scan_1.pdf"])
    trigger = FakeScanTrigger(result=result)
    server = start_bridge_server(lambda: profiles, trigger, port=unused_tcp_port)
    try:
        status, body = _post(f"http://127.0.0.1:{unused_tcp_port}/scan/Dossiary%20Scan")
    finally:
        server.shutdown()

    assert status == 200
    assert body["ok"] is True
    assert trigger.called_with == profiles[0]


def test_bridge_server_round_trip_unknown_profile_is_404(unused_tcp_port):
    server = start_bridge_server(lambda: [], FakeScanTrigger(), port=unused_tcp_port)
    try:
        status, body = _post(f"http://127.0.0.1:{unused_tcp_port}/scan/Nope")
    finally:
        server.shutdown()

    assert status == 404


def test_bridge_server_round_trip_busy_is_409(unused_tcp_port):
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    trigger = FakeScanTrigger(raises=ScanBusyError())
    server = start_bridge_server(lambda: profiles, trigger, port=unused_tcp_port)
    try:
        status, _ = _post(f"http://127.0.0.1:{unused_tcp_port}/scan/Dossiary%20Scan")
    finally:
        server.shutdown()

    assert status == 409


def test_bridge_server_response_has_cors_header(unused_tcp_port):
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    result = ScanResult(ok=True, partial=False, message="ok", output_paths=[])
    server = start_bridge_server(lambda: profiles, FakeScanTrigger(result=result), port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/scan/Dossiary%20Scan", method="POST", data=b"")
        with urllib.request.urlopen(req) as resp:
            assert resp.headers["Access-Control-Allow-Origin"] == "*"
    finally:
        server.shutdown()


def test_bridge_server_options_preflight_is_handled(unused_tcp_port):
    server = start_bridge_server(lambda: [], FakeScanTrigger(), port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/scan/Dossiary%20Scan", method="OPTIONS")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 204
            assert resp.headers["Access-Control-Allow-Origin"] == "*"
    finally:
        server.shutdown()


def test_bridge_server_strips_query_string_from_profile_name(unused_tcp_port):
    # POST /scan/Dossiary%20Scan?t=123 must still resolve to the profile
    # named "Dossiary Scan" -- do_POST has to strip the query string
    # before unquoting/matching the path, not include it in the decoded
    # name.
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    result = ScanResult(ok=True, partial=False, message="/tmp/scans/scan_1.pdf", output_paths=["/tmp/scans/scan_1.pdf"])
    trigger = FakeScanTrigger(result=result)
    server = start_bridge_server(lambda: profiles, trigger, port=unused_tcp_port)
    try:
        status, body = _post(f"http://127.0.0.1:{unused_tcp_port}/scan/Dossiary%20Scan?t=123")
    finally:
        server.shutdown()

    assert status == 200
    assert body["ok"] is True
    assert trigger.called_with == profiles[0]


def test_bridge_server_unhandled_method_still_has_cors_header(unused_tcp_port):
    # Every response -- success or error, including methods this bridge
    # never explicitly handles (GET, PUT, ...) -- must carry
    # Access-Control-Allow-Origin, not just the do_POST/do_OPTIONS paths.
    server = start_bridge_server(lambda: [], FakeScanTrigger(), port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/scan/Default", method="GET")
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


def test_bridge_server_reads_profiles_live_not_a_snapshot(unused_tcp_port):
    # get_profiles is called fresh on every request, not captured once at
    # start_bridge_server() time -- proves a profile added via Add Profile
    # while the bridge is already running is immediately reachable.
    profiles = []
    server = start_bridge_server(lambda: profiles, FakeScanTrigger(result=ScanResult(True, False, "ok", [])), port=unused_tcp_port)
    try:
        status, _ = _post(f"http://127.0.0.1:{unused_tcp_port}/scan/Late")
        assert status == 404
        profiles.append(Profile(name="Late", destination="/tmp/scans"))
        status, _ = _post(f"http://127.0.0.1:{unused_tcp_port}/scan/Late")
        assert status == 200
    finally:
        server.shutdown()
