import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from PIL import Image

from scanix500.menubar.bridge import RemoteOptions, make_handler_class
from scanix500.menubar.fakes import FakeBackend
from scanix500.menubar.pairing import PairingStore
from scanix500.server.button_events import ButtonEvents
from scanix500.server.button_pairing import ButtonPairing
from scanix500.server.button_scans import ButtonScanStore

FP = "AB" * 32


def _pdf(path, width):
    Image.new("RGB", (width, 40)).save(path, "PDF")
    return path


def _serve(tmp_path, store, remote=True, events=None):
    pairing = PairingStore(tmp_path / "p.json")
    buttons = ButtonPairing(pairing.issue_token)
    opts = RemoteOptions(fingerprint=FP, name="pi", button_pairing=buttons) if remote else None
    handler = make_handler_class(lambda: str(tmp_path), None, pairing, FakeBackend(), remote=opts,
                                 button_scans=store, button_events=events)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}", pairing.issue_token("Mac")


@pytest.fixture
def env(tmp_path):
    clock = iter(range(1_700_000_000, 1_700_100_000, 10))
    store = ButtonScanStore(tmp_path / "store", clock=lambda: next(clock))
    first = store.add(_pdf(tmp_path / "a.pdf", 50), 'We"ird\\ ü中.pdf', 1, skip_ocr=True, languages=[])
    second = store.add(_pdf(tmp_path / "b.pdf", 60), "plain.pdf", 1, skip_ocr=False, languages=["eng"])
    httpd, base, token = _serve(tmp_path, store)
    yield base, token, store, first, second
    httpd.shutdown()


def call(base, path, method="GET", token=None, headers=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, method=method, headers=h)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_list_is_oldest_first_with_ocr_states(env):
    base, token, store, first, second = env
    status, raw, headers = call(base, "/button-scans", token=token)
    scans = json.loads(raw)["scans"]
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    assert [s["id"] for s in scans] == [first, second]
    assert [s["ocr"] for s in scans] == ["done", "pending"]
    assert set(scans[0]) == {"id", "name", "pages", "bytes", "created", "ocr"}


def test_all_three_need_a_token(env):
    base, _, _, first, _ = env
    for method, path in (("GET", "/button-scans"), ("GET", f"/button-scans/{first}"),
                         ("DELETE", f"/button-scans/{first}")):
        status, raw, _ = call(base, path, method)
        assert status == 401 and "message" in json.loads(raw)


def test_download_sends_the_pdf_with_a_safe_name(env):
    base, token, store, first, second = env
    status, raw, headers = call(base, f"/button-scans/{first}", token=token)
    assert status == 200 and headers["Content-Type"] == "application/pdf"
    assert raw == store.get(first)[0].read_bytes() and headers["Content-Length"] == str(len(raw))
    disposition = headers["Content-Disposition"]
    disposition.encode("ascii")
    assert disposition.startswith('attachment; filename="We_ird_ __.pdf"')
    assert "filename*=UTF-8''We%22ird%5C%20%C3%BC%E4%B8%AD.pdf" in disposition
    assert "X-Scanix500-Ocr" not in headers


def test_pending_ocr_is_flagged(env):
    base, token, _, _, second = env
    _, _, headers = call(base, f"/button-scans/{second}", token=token)
    assert headers["X-Scanix500-Ocr"] == "pending"
    assert headers["Content-Disposition"].startswith('attachment; filename="plain.pdf"')


@pytest.mark.parametrize("path", ["/button-scans/unknownunknownunknownunknown", "/button-scans/..%2Fetc%2Fpasswd",
                                  "/button-scans/short", "/button-scans/../etc/passwd"])
def test_bad_ids_are_404(env, path):
    base, token, *_ = env
    for method in ("GET", "DELETE"):
        status, raw, _ = call(base, path, method, token)
        assert status == 404 and "message" in json.loads(raw)


def test_delete_then_gone(env):
    base, token, _, first, _ = env
    assert call(base, f"/button-scans/{first}", "DELETE", token)[0] == 204
    assert call(base, f"/button-scans/{first}", token=token)[0] == 404
    assert call(base, f"/button-scans/{first}", "DELETE", token)[0] == 404


def test_wrong_methods_are_405_with_allow(env):
    base, token, _, first, _ = env
    status, _, headers = call(base, "/button-scans", "POST", token)
    assert status == 405 and headers["Allow"] == "GET, OPTIONS"
    status, _, headers = call(base, f"/button-scans/{first}", "POST", token)
    assert status == 405 and headers["Allow"] == "GET, DELETE, OPTIONS"


def test_origin_is_refused_first(env):
    base, token, _, first, _ = env
    for path in ("/button-scans", f"/button-scans/{first}"):
        assert call(base, path, token=token, headers={"Origin": "http://localhost:8080"})[0] == 403


def test_health_reports_button_scans_in_remote_mode(env):
    base, *_ = env
    assert json.loads(call(base, "/health")[1])["buttonScans"] is True


def test_no_store_means_nothing_new(tmp_path):
    httpd, base, token = _serve(tmp_path, None)
    try:
        assert "buttonScans" not in json.loads(call(base, "/health")[1])
        assert call(base, "/button-scans", token=token)[0] == 404
    finally:
        httpd.shutdown()


def test_local_mode_has_no_button_scans(tmp_path):
    store = ButtonScanStore(tmp_path / "store")
    httpd, base, token = _serve(tmp_path, store, remote=False)
    try:
        assert "buttonScans" not in json.loads(call(base, "/health")[1])
        assert call(base, "/button-scans", token=token)[0] == 404
    finally:
        httpd.shutdown()


def test_fake_store_has_the_two_fixed_scans(tmp_path):
    from scanix500.menubar.fakes import fake_button_scan_store
    scans = fake_button_scan_store(tmp_path / "fake").list()
    assert [(s["name"], s["ocr"]) for s in scans] == [("fake-1.pdf", "done"), ("fake-2.pdf", "pending")]


def _health(base, token=None):
    status, raw, _ = call(base, "/health", token=token)
    assert status == 200
    return json.loads(raw)


def test_health_announces_button_events_to_paired_callers(tmp_path):
    events = ButtonEvents()
    httpd, base, token = _serve(tmp_path, ButtonScanStore(tmp_path / "s"), events=events)
    try:
        assert _health(base, token)["buttonEvent"] == {"seq": 0}
        events.record("color")
        body = _health(base, token)
        assert body["buttonEvent"] == {"seq": 1, "button": "color"}
        assert body["buttonScans"] is True
    finally:
        httpd.shutdown()


def test_health_hides_button_events_without_a_valid_token(tmp_path):
    events = ButtonEvents()
    events.record("bw")
    httpd, base, token = _serve(tmp_path, ButtonScanStore(tmp_path / "s"), events=events)
    try:
        assert "buttonEvent" not in _health(base)
        assert "buttonEvent" not in _health(base, "not-a-token")
    finally:
        httpd.shutdown()


def test_health_has_no_button_event_without_a_store(tmp_path):
    httpd, base, token = _serve(tmp_path, None, events=ButtonEvents())
    try:
        body = _health(base, token)
        assert "buttonEvent" not in body and "buttonScans" not in body
    finally:
        httpd.shutdown()
