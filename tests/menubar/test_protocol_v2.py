import base64

import pytest
from pypdf import PdfWriter

from scanix500.menubar.protocol_v2 import (
    ALL_BLANK_MESSAGE, EMPTY_FEEDER_MESSAGE, IX500, NOT_CONNECTED_MESSAGE, ProtocolError,
    error_body, pages_message, profile_for, response_from_result, validate,
)
from scanix500.menubar.runner import ScanResult


def _status(body):
    with pytest.raises(ProtocolError) as info:
        validate(body, [IX500])
    return info.value.status


def test_ix500_entry_is_exact():
    assert IX500 == {"id": "sane:ix500", "name": "ScanSnap iX500", "sources": ["feeder"], "duplex": True,
                     "colorModes": ["color", "gray", "bw"], "resolutions": [300, 600],
                     "extras": ["splitOnBlank", "skipBlankPages"]}


def test_defaults():
    scanner, settings = validate({"scanner": "sane:ix500"}, [IX500])
    assert scanner["id"] == "sane:ix500"
    assert settings == {"source": "feeder", "duplex": False, "colorMode": "color", "resolution": 300,
                        "format": "pdf", "extras": {}}


@pytest.mark.parametrize("body, status", [
    ([], 400), ({}, 400), ({"scanner": 1}, 400),
    ({"scanner": "nope"}, 404),
    ({"scanner": "sane:ix500", "resolution": 300.0}, 400),
    ({"scanner": "sane:ix500", "resolution": True}, 400),
    ({"scanner": "sane:ix500", "duplex": "yes"}, 400),
    ({"scanner": "sane:ix500", "extras": {"splitOnBlank": "yes"}}, 400),
    ({"scanner": "sane:ix500", "source": "flatbed"}, 422),
    ({"scanner": "sane:ix500", "colorMode": "sepia"}, 422),
    ({"scanner": "sane:ix500", "resolution": 150}, 422),
    ({"scanner": "sane:ix500", "format": "tiff"}, 422),
    ({"scanner": "sane:ix500", "extras": {"magic": True}}, 422),
])
def test_validation_errors(body, status):
    assert _status(body) == status


def test_unknown_extra_set_to_false_is_accepted():
    validate({"scanner": "sane:ix500", "extras": {"magic": False}}, [IX500])


def test_pages_message():
    assert pages_message(1) == "1 page scanned."
    assert pages_message(3) == "3 pages scanned."


@pytest.mark.parametrize("extras, flags", [
    ({}, (True, False, False)),
    ({"skipBlankPages": True}, (False, False, False)),
    ({"splitOnBlank": True}, (False, True, True)),
    ({"splitOnBlank": True, "skipBlankPages": True}, (False, True, False)),
])
def test_profile_for_maps_extras(extras, flags):
    settings = {"source": "feeder", "duplex": True, "colorMode": "bw", "resolution": 600,
                "format": "pdf", "extras": extras}
    p = profile_for(settings, "/dest")
    assert (p.skip_blank_filter, p.split_on_blank, p.keep_blank_pages) == flags
    assert (p.destination, p.skip_ocr, p.duplex, p.color_mode, p.resolution) == ("/dest", False, True, "bw", 600)


def _pdf(path, pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    with open(path, "wb") as f:
        writer.write(f)
    return str(path)


def test_success_response_counts_pages(tmp_path):
    paths = [_pdf(tmp_path / "scan_1.pdf", 2), _pdf(tmp_path / "scan_2.pdf", 1)]
    status, body = response_from_result(ScanResult(ok=True, partial=False, message="x", output_paths=paths))
    assert status == 200
    assert body["ok"] is True and body["partial"] is False and body["message"] == "3 pages scanned."
    assert [(f["name"], f["pages"]) for f in body["files"]] == [("scan_1.pdf", 2), ("scan_2.pdf", 1)]
    assert base64.b64decode(body["files"][0]["data"]).startswith(b"%PDF")


def test_partial_response_keeps_pages_and_drops_the_path(tmp_path):
    path = _pdf(tmp_path / "scan.pdf", 2)
    result = ScanResult(ok=False, partial=True,
                        message=f"Multi-feed detected at sheet 2; processing 2 pages captured before the jam. — partial scan saved to {path}",
                        output_paths=[path])
    status, body = response_from_result(result)
    assert status == 200 and body["ok"] is False and body["partial"] is True
    assert body["message"] == "Multi-feed detected at sheet 2; processing 2 pages captured before the jam."
    assert body["files"][0]["pages"] == 2


@pytest.mark.parametrize("result, status, message", [
    (ScanResult(True, False, "No pages scanned.", []), 500, EMPTY_FEEDER_MESSAGE),
    (ScanResult(True, False, "All pages were blank; no PDF written.", []), 500, ALL_BLANK_MESSAGE),
    (ScanResult(False, False, "Scanner not found: No iX500 found via SANE fujitsu backend", []), 503, NOT_CONNECTED_MESSAGE),
    (ScanResult(False, False, "Scanner not found: Failed to open SANE device: Device busy", []), 503,
     "The ScanSnap iX500 can't be used right now: Failed to open SANE device: Device busy"),
    (ScanResult(False, False, "Scanner setup failed: The scanner refused a setting: bad", []), 500,
     "Scanner setup failed: The scanner refused a setting: bad"),
    (ScanResult(False, False, "Failed to write output to /x: denied", []), 500, "Failed to write output to /x: denied"),
])
def test_failures(result, status, message):
    got_status, body = response_from_result(result)
    assert (got_status, body) == (status, error_body(message))


def test_error_body_has_both_keys():
    assert error_body("Nope.") == {"ok": False, "partial": False, "message": "Nope.", "error": "Nope."}
