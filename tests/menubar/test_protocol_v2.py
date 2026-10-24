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
                     "extras": ["splitOnBlank", "skipBlankPages"],
                     "ocr": {"languages": ["deu", "eng", "fra", "spa", "chi_sim", "chi_tra"]}}


def test_defaults():
    scanner, settings = validate({"scanner": "sane:ix500"}, [IX500])
    assert scanner["id"] == "sane:ix500"
    assert settings == {"source": "feeder", "duplex": False, "colorMode": "color", "resolution": 300,
                        "format": "pdf", "area": None, "extras": {}, "ocr": False, "ocrLanguages": []}


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


@pytest.mark.parametrize("body, status", [
    ({"scanner": "sane:ix500", "ocr": "yes"}, 400),
    ({"scanner": "sane:ix500", "ocr": True, "ocrLanguages": "deu"}, 400),
    ({"scanner": "sane:ix500", "ocr": True, "ocrLanguages": [1]}, 400),
    ({"scanner": "sane:ix500", "ocr": "yes", "ocrLanguages": ["xyz"]}, 400),
    ({"scanner": "sane:ix500", "ocr": True, "ocrLanguages": ["xyz"]}, 422),
])
def test_ocr_validation(body, status):
    assert _status(body) == status


def test_ocr_defaults_and_type_only_without_ocr():
    _, s = validate({"scanner": "sane:ix500", "ocrLanguages": ["xyz"]}, [IX500])
    assert (s["ocr"], s["ocrLanguages"]) == (False, ["xyz"])
    _, s = validate({"scanner": "sane:ix500", "ocr": True, "ocrLanguages": ["fra", "fra", "eng"]}, [IX500])
    assert (s["ocr"], s["ocrLanguages"]) == (True, ["fra", "eng"])


def test_profile_skips_ocr_when_the_job_does_it():
    settings = {"source": "feeder", "duplex": True, "colorMode": "gray", "resolution": 300,
                "format": "pdf", "extras": {}, "ocr": True, "ocrLanguages": []}
    assert profile_for(settings, "/d").skip_ocr is True
    assert profile_for({**settings, "ocr": False}, "/d").skip_ocr is False


def test_profile_for_carries_the_scanner_id():
    settings = {"source": "flatbed", "duplex": False, "colorMode": "gray", "resolution": 300,
                "format": "pdf", "extras": {}}
    assert profile_for(settings, "/d").scanner == "sane:ix500"
    assert profile_for(settings, "/d", "sane:canoscan-8800f").scanner == "sane:canoscan-8800f"


def test_not_found_is_503_with_the_scanners_own_message():
    from scanix500.scanners import CANOSCAN_8800F_ID, get_scanner
    result = ScanResult(False, False, "Scanner not found: No CanoScan 8800F found via SANE pixma backend", [])
    assert response_from_result(result, CANOSCAN_8800F_ID) == (
        503, error_body(get_scanner(CANOSCAN_8800F_ID).not_connected_message))
    # The iX500's text doesn't match the 8800F's scanner.
    ix = ScanResult(False, False, "Scanner not found: No iX500 found via SANE fujitsu backend", [])
    status, body = response_from_result(ix, CANOSCAN_8800F_ID)
    assert status == 503 and body["message"].startswith("The Canon CanoScan 8800F can't be used right now")


def test_a_driver_failure_names_the_scanner():
    from scanix500.scanners import CANOSCAN_8800F_ID
    result = ScanResult(False, False, "Scanner not found: Failed to open SANE device: busy", [])
    status, body = response_from_result(result, CANOSCAN_8800F_ID)
    assert (status, body["message"]) == (
        503, "The Canon CanoScan 8800F can't be used right now: Failed to open SANE device: busy")


def _photo(**body):
    from scanix500.scanners import CANOSCAN_8800F
    return validate({"scanner": "sane:canoscan-8800f", **body}, [CANOSCAN_8800F])


def test_the_8800f_defaults_are_pdf_and_a4():
    _, settings = _photo()
    assert (settings["format"], settings["area"]) == ("pdf", "a4")


def test_the_ix500_has_no_area_and_stays_pdf():
    from scanix500.scanners import IX500
    _, settings = validate({"scanner": "sane:ix500"}, [IX500])
    assert (settings["format"], settings["area"]) == ("pdf", None)


def test_an_image_request_is_accepted():
    _, settings = _photo(format="jpeg", area="10x15", resolution=1200)
    assert (settings["format"], settings["area"], settings["resolution"]) == ("jpeg", "10x15", 1200)
    _photo(format="png", colorMode="bw", area="a6")  # PNG can be 1-bit


@pytest.mark.parametrize("body", [
    {"format": "tiff"},
    {"area": "letter"},
    {"format": "jpeg", "colorMode": "bw"},
    {"format": "jpeg", "ocr": True},
    {"format": "png", "ocr": True},
    {"area": "a4", "resolution": 1200},
    {"format": "png", "area": "a4", "resolution": 1200},
])
def test_unsupported_image_requests_are_422(body):
    with pytest.raises(ProtocolError) as info:
        _photo(**body)
    assert info.value.status == 422 and info.value.message


def test_the_refusal_messages_say_what_to_change():
    with pytest.raises(ProtocolError) as info:
        _photo(area="a4", resolution=1200)
    assert info.value.message == ("A4 at 1200 dpi is too large (139 megapixels; the limit is 100): "
                                  "choose a smaller area or a lower resolution.")
    with pytest.raises(ProtocolError) as info:
        _photo(format="jpeg", colorMode="bw")
    assert info.value.message == "JPEG can't be black and white; use PNG or PDF."
    with pytest.raises(ProtocolError) as info:
        _photo(format="jpeg", ocr=True)
    assert "searchable PDF" in info.value.message


def test_the_size_limit_is_inclusive(monkeypatch):
    from scanix500.scanners import pixel_count
    exact = pixel_count("10x15", 600)
    monkeypatch.setattr("scanix500.menubar.protocol_v2.MAX_PIXELS", exact)
    _photo(area="10x15", resolution=600)
    monkeypatch.setattr("scanix500.menubar.protocol_v2.MAX_PIXELS", exact - 1)
    with pytest.raises(ProtocolError) as info:
        _photo(area="10x15", resolution=600)
    assert info.value.status == 422 and "too large" in info.value.message


def test_the_ix500_refuses_images_and_areas():
    from scanix500.scanners import IX500
    for body in ({"format": "jpeg"}, {"area": "a4"}):
        with pytest.raises(ProtocolError) as info:
            validate({"scanner": "sane:ix500", **body}, [IX500])
        assert info.value.status == 422


def test_a_wrong_area_type_is_400_before_any_422():
    with pytest.raises(ProtocolError) as info:
        _photo(area=5, format="jpeg", colorMode="bw")
    assert info.value.status == 400


def test_profile_for_carries_format_and_area():
    settings = {"source": "flatbed", "duplex": False, "colorMode": "gray", "resolution": 600,
                "format": "png", "area": "13x18", "extras": {}}
    p = profile_for(settings, "/d", "sane:canoscan-8800f")
    assert (p.format, p.area) == ("png", "13x18")
    legacy = {k: v for k, v in settings.items() if k not in ("format", "area")}
    p = profile_for(legacy, "/d")
    assert (p.format, p.area) == ("pdf", "a4")


def _image_file(tmp_path, name, fmt):
    from PIL import Image
    path = tmp_path / name
    Image.new("RGB", (20, 20), "white").save(path, fmt)
    return str(path)


def test_image_results_answer_with_one_page_per_file_and_an_images_message(tmp_path):
    one = ScanResult(True, False, "x", [_image_file(tmp_path, "scan_1.jpg", "JPEG")])
    status, body = response_from_result(one)
    assert status == 200 and body["message"] == "1 image scanned."
    assert [(f["name"], f["pages"]) for f in body["files"]] == [("scan_1.jpg", 1)]
    two = ScanResult(True, False, "x", [_image_file(tmp_path, "a.png", "PNG"), _image_file(tmp_path, "b.png", "PNG")])
    assert response_from_result(two)[1]["message"] == "2 images scanned."


def test_images_message():
    from scanix500.menubar.protocol_v2 import images_message
    assert images_message(1) == "1 image scanned." and images_message(3) == "3 images scanned."
