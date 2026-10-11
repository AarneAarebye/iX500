import base64

import pytest
from pypdf import PdfReader
import io

from scanix500.menubar.bridge import ScanBusyError
from scanix500.menubar.fakes import FAKE_PAIRING_CODE, FAKE_SCANNERS, FakeBackend, FakePairing, make_pdf
from scanix500.menubar.pairing import PairingError
from scanix500.menubar.protocol_v2 import validate


def _scan(scanner_id, **fields):
    backend = FakeBackend()
    scanner, settings = validate({"scanner": scanner_id, **fields}, backend.scanners())
    return backend.scan(scanner, settings)


def test_seven_fakes():
    assert [s["id"] for s in FAKE_SCANNERS][:7] == ["fake:flatbed", "fake:feeder", "fake:splitter", "fake:jam",
                                                 "fake:slow", "fake:offline", "fake:empty"]


def test_feeder_duplex_six_pages():
    status, body = _scan("fake:feeder", source="feeder", duplex=True)
    assert status == 200 and body["message"] == "6 pages scanned."
    assert len(PdfReader(io.BytesIO(base64.b64decode(body["files"][0]["data"]))).pages) == 6


@pytest.mark.parametrize("extras, pages", [({}, [4]), ({"skipBlankPages": True}, [3]),
                                           ({"splitOnBlank": True}, [2, 1])])
def test_splitter(extras, pages):
    status, body = _scan("fake:splitter", extras=extras)
    assert [f["pages"] for f in body["files"]] == pages
    assert len({f["name"] for f in body["files"]}) == len(pages)


def test_jam_is_partial():
    status, body = _scan("fake:jam")
    assert status == 200 and body["partial"] is True and body["ok"] is False and "jam" in body["message"]


def test_offline_and_empty():
    assert _scan("fake:offline")[0] == 503
    status, body = _scan("fake:empty")
    assert status == 500 and body["message"] == "No paper in the feeder."


def test_busy_raises():
    backend = FakeBackend()
    backend._lock.acquire()
    scanner, settings = validate({"scanner": "fake:flatbed"}, backend.scanners())
    with pytest.raises(ScanBusyError):
        backend.scan(scanner, settings)


def test_make_pdf_pages():
    assert len(PdfReader(io.BytesIO(make_pdf(["a", "", "b"]))).pages) == 3


def test_fake_pairing():
    pairing = FakePairing()
    token = pairing.pair(FAKE_PAIRING_CODE)
    assert pairing.is_valid(token) and pairing.pair(FAKE_PAIRING_CODE) != token
    assert not pairing.is_valid("nope") and not pairing.is_valid(None)
    with pytest.raises(PairingError, match="Pair a browser"):
        pairing.pair("123456")


def test_fake_ocr_scanner_and_job():
    backend = FakeBackend()
    assert [s["id"] for s in backend.scanners()][-1] == "fake:ocr"
    scanner, settings = validate({"scanner": "fake:ocr", "ocr": True}, backend.scanners())
    status, body = backend.scan(scanner, settings)
    f = body["files"][0]
    assert b"Flatbed page 1" not in base64.b64decode(f["data"])
    assert backend.ocr_job(f["ocrJob"])[0] == 202
