import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from scanix500.menubar.bridge import RealBackend, RemoteOptions, make_handler_class
from scanix500.menubar.fakes import FakeBackend
from scanix500.menubar.pairing import PairingStore
from scanix500.menubar.runner import ScanResult
from scanix500.server.button_pairing import ButtonPairing

FP = "AB" * 32


@pytest.fixture
def server(tmp_path):
    pairing = PairingStore(tmp_path / "p.json")
    buttons = ButtonPairing(pairing.issue_token)
    handler = make_handler_class(lambda: str(tmp_path), None, pairing, FakeBackend(),
                                 remote=RemoteOptions(fingerprint=FP, name="raspberrypi", button_pairing=buttons))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", pairing, buttons
    httpd.shutdown()


def call(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read() or b"{}"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


def test_health_names_the_server_and_its_fingerprint(server):
    base, _, _ = server
    status, body, headers = call(f"{base}/health")
    assert status == 200
    assert body["remote"] is True and body["fingerprint"] == FP and body["name"] == "raspberrypi"
    assert body["service"] == "scanix500-server" and body["protocol"] == 2 and body["paired"] is False
    assert "Access-Control-Allow-Origin" not in headers


def test_any_web_page_origin_is_refused(server):
    base, _, _ = server
    for origin in ("null", "http://localhost:8080", "https://example.com"):
        status, _, headers = call(f"{base}/health", headers={"Origin": origin})
        assert status == 403 and "Access-Control-Allow-Origin" not in headers


def test_scanners_need_a_token(server):
    base, pairing, _ = server
    status, body, _ = call(f"{base}/scanners")
    assert status == 401 and "Add network scanner" in body["message"]
    token = pairing.issue_token("Mac")
    status, body, _ = call(f"{base}/scanners", headers={"Authorization": f"Bearer {token}"})
    assert status == 200 and body["scanners"]


def test_code_pairing_records_the_client_name(server):
    base, pairing, _ = server
    code = pairing.open_window()
    status, body, _ = call(f"{base}/pair", "POST", {"code": code, "client": "Office iMac"})
    assert status == 200 and body["token"]
    assert [c["name"] for c in pairing.clients()] == ["Office iMac"]


def test_button_pairing_round_trip(server):
    base, pairing, buttons = server
    status, body, _ = call(f"{base}/pair/button", "POST", {"client": "Office iMac"})
    assert status == 202
    rid = body["request"]
    status, body, _ = call(f"{base}/pair/button/{rid}")
    assert status == 202 and body["state"] == "pending"
    assert buttons.press()
    status, body, _ = call(f"{base}/pair/button/{rid}")
    assert status == 200 and body["token"] and pairing.is_valid(body["token"])
    status, _, _ = call(f"{base}/pair/button/{rid}")
    assert status == 404


def test_button_pairing_conflict_and_bad_requests(server):
    base, _, _ = server
    a = call(f"{base}/pair/button", "POST", {"client": "A"})[1]["request"]
    call(f"{base}/pair/button", "POST", {"client": "B"})
    assert call(f"{base}/pair/button/{a}")[0] == 409
    assert call(f"{base}/pair/button", "POST", {"client": 5})[0] == 400
    assert call(f"{base}/pair/button", "GET")[0] == 405


def test_button_pairing_rate_limit(server):
    base, _, _ = server
    for i in range(10):
        call(f"{base}/pair/button", "POST", {"client": f"M{i}"})
    assert call(f"{base}/pair/button", "POST", {"client": "X"})[0] == 429


def test_real_backend_without_keeping_outputs_deletes_the_scan(tmp_path):
    pdf = tmp_path / "scan_1.pdf"
    from scanix500.pdf_builder import build_pdf
    from PIL import Image
    build_pdf([Image.new("L", (100, 100), 0)], pdf, ocr=False)

    class Trigger:
        def trigger(self, profile):
            return ScanResult(ok=True, partial=False, message=str(pdf), output_paths=[str(pdf)])

    backend = RealBackend(lambda: str(tmp_path), Trigger(), ocr_jobs=False, keep_outputs=False)
    settings = {"extras": {}, "duplex": True, "colorMode": "color", "resolution": 300}
    status, body = backend.scan({}, settings)
    assert status == 200 and body["files"]
    assert not pdf.exists()
