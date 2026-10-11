# scanix500 protocol version 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** scanix500's HTTP bridge speaks version 2 of the Dossiary scan bridge protocol (`GET /scanners`, JSON `POST /scan`), so Dossiary's scan dialog shows the iX500's real settings (both sides, color/gray/black & white, 300/600 dpi, split on blank, skip blank pages), while keeping the version 1 request for older Dossiary releases.

**Architecture:** Step 4 of `docs/superpowers/specs/2026-10-09-system-scanner-helper-design.md` in the sibling Dossiary repo (`/Users/aarneaarebye/Projects/Paperless/Dossiary`). The wire contract is `PROTOCOL.md` in `/Users/aarneaarebye/Projects/Paperless/dossiary-scan-helper`. The scan pipeline (`scanix500` CLI) gains settings flags; a new pure module `protocol_v2.py` validates requests and shapes responses; `bridge.py` routes version 1 and version 2 requests in the protocol's check order; a headless `scanix500-bridge --fake-scanners` runs the protocol's fake scanners so dossiary-scan-helper's conformance suite can test this bridge without hardware.

**Tech Stack:** Python 3.11+, stdlib `http.server`, Pillow, pypdf, python-sane (hardware only), pytest. The repo's venv is `.venv/` (`.venv/bin/python -m pytest`).

## Global Constraints

- Repo: `/Users/aarneaarebye/Projects/iX500` (package `scanix500`). Baseline: `.venv/bin/python -m pytest -q` → 134 passed.
- Protocol: dossiary-scan-helper's `PROTOCOL.md` is the contract. Check order per request: Origin 403 → OPTIONS 204 (any path) → unknown path 404 / wrong method 405 with `Allow` → `/scanners` and `/scan` without a valid token 401 → endpoint checks.
- Version 2 error bodies are `{"ok": false, "partial": false, "message": "<plain English for a person>", "error": "<same message>"}` (the extra `error` key keeps Dossiary 1.43, which reads `error`, working).
- Version 1 stays: a `POST /scan` **with a query string** is a version 1 request, answered exactly as today (`route_scan_request`, `{"error"}` bodies, the 401 message). Without a query string, `POST /scan` is version 2.
- The iX500's version 2 entry, exactly: `{"id": "sane:ix500", "name": "ScanSnap iX500", "sources": ["feeder"], "duplex": true, "colorModes": ["color", "gray", "bw"], "resolutions": [300, 600], "extras": ["splitOnBlank", "skipBlankPages"]}`. It is always listed (no SANE probe from the HTTP thread); a scan with the iX500 unplugged answers 503.
- OCR stays on for version 2 scans (ocrmypdf `deu+eng`, as today).
- Black & white is scanned as SANE `Gray` and thresholded to 1-bit after trimming and the blank check (both need gray levels).
- `GET /health`: `{"ok": true, "helper": "scanix500", "version": "0.4.0", "protocol": 2, "paired": <bool>, "service": "scanix500-bridge"}`. Version becomes `0.4.0` (`pyproject.toml` and `scanix500.__version__`).
- Request bodies: no required `Content-Type`; missing `Content-Length` = empty body; negative/non-numeric `Content-Length` or over 1,000,000 bytes → 400.
- Fake scanner mode follows `PROTOCOL.md` "Fake scanner mode" exactly (seven scanners, code `000000`, exact English messages; a refused pairing message contains `Pair a browser`).
- Bind `127.0.0.1` only; default port 8765.
- No rumps/AppKit import in `bridge.py`, `protocol_v2.py`, `fakes.py`, `bridge_main.py`.
- Hardware behaviour (SANE source/mode names, black & white threshold, blank detection on gray/bw) is checked by hand at the end, never mocked beyond the existing `SaneDevice` protocol.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01AnMx4wtUcaKMWsQm9eXUTo
  ```

---

### Task 1: Scan settings in the pipeline

**Files:**
- Modify: `src/scanix500/capture.py`, `src/scanix500/blank_filter.py`, `src/scanix500/pdf_builder.py`, `src/scanix500/cli.py`, `src/scanix500/menubar/profiles.py`, `src/scanix500/menubar/runner.py`
- Create: `src/scanix500/bw.py`
- Test: `tests/test_capture.py`, `tests/test_blank_filter.py`, `tests/test_pdf_builder.py`, `tests/test_cli.py`, `tests/menubar/test_runner.py`, `tests/menubar/test_profiles.py`, create `tests/test_bw.py`

**Interfaces:**
- Produces: `PagePair(front, back=None)`; `capture_pages(device, *, duplex=True)`; `PySaneDevice(*, duplex=True, color_mode="color", resolution=300)`; `SANE_MODES`; `filter_pages(pages, *, threshold=0.02, split_on_blank=False, drop_blank_pages=True)`; `to_black_and_white(image) -> Image` (mode `"1"`, keeps `info["dpi"]`); CLI flags `--simplex`, `--mode {color,gray,bw}`, `--resolution N`, `--keep-blank-pages`; `Profile` gains `duplex: bool = True`, `color_mode: str = "color"`, `resolution: int = 300`, `keep_blank_pages: bool = False` (in that order, after `split_on_blank`); `runner._build_argv` adds the flags only for non-default values.

- [ ] **Step 1: Write the failing tests**

`tests/test_capture.py` (add):

```python
def test_capture_pages_simplex_reads_one_frame_per_sheet():
    device = FakeSaneDevice(frames=[_img(), _img(), _img()])
    pages = capture_pages(device, duplex=False)
    assert len(pages) == 3
    assert all(p.back is None for p in pages)


def test_capture_pages_simplex_jam_keeps_earlier_sheets():
    device = FakeSaneDevice(frames=[_img(), RuntimeError("jam")])
    with pytest.raises(MultiFeedError) as info:
        capture_pages(device, duplex=False)
    assert len(info.value.pages_captured) == 1
```

Use the file's existing `FakeSaneDevice`/image helpers; if they're named differently, adapt the names, not the behaviour (a frame that is an exception instance is raised by `read_frame`; if the existing fake doesn't support that, extend it).

`tests/test_blank_filter.py` (add; `_blank()`/`_content()` = the file's existing blank/content image helpers):

```python
def test_simplex_pairs_have_no_back():
    pages = [PagePair(_content(), None), PagePair(_blank(), None), PagePair(_content(), None)]
    assert [len(p) for p in filter_pages(pages)] == [2]


def test_simplex_blank_sheet_splits():
    pages = [PagePair(_content(), None), PagePair(_blank(), None), PagePair(_content(), None)]
    assert [len(p) for p in filter_pages(pages, split_on_blank=True)] == [1, 1]


def test_keep_blank_pages_keeps_blank_backs_but_still_splits():
    pages = [PagePair(_content(), _blank()), PagePair(_blank(), _blank()), PagePair(_content(), _blank())]
    result = filter_pages(pages, split_on_blank=True, drop_blank_pages=False)
    assert [len(p) for p in result] == [2, 2]


def test_keep_blank_pages_without_split_keeps_everything():
    pages = [PagePair(_content(), _blank()), PagePair(_blank(), _blank())]
    assert [len(p) for p in filter_pages(pages, drop_blank_pages=False)] == [4]
```

`tests/test_bw.py`:

```python
from PIL import Image

from scanix500.bw import BW_THRESHOLD, to_black_and_white


def test_to_black_and_white_thresholds_gray_and_keeps_dpi():
    image = Image.new("L", (4, 1))
    image.putdata([0, BW_THRESHOLD - 1, BW_THRESHOLD, 255])
    image.info["dpi"] = (600, 600)
    out = to_black_and_white(image)
    assert out.mode == "1"
    assert [0 if v == 0 else 255 for v in out.getdata()] == [0, 0, 255, 255]
    assert out.info["dpi"] == (600, 600)


def test_to_black_and_white_accepts_color():
    image = Image.new("RGB", (2, 2), (250, 250, 250))
    assert to_black_and_white(image).mode == "1"
```

`tests/test_pdf_builder.py` (add):

```python
def test_bw_and_gray_pages_are_not_converted_to_color(tmp_path):
    from pypdf import PdfReader
    bw = Image.new("1", (100, 100), 1); bw.info["dpi"] = (300, 300)
    gray = Image.new("L", (100, 100), 200); gray.info["dpi"] = (300, 300)
    out = tmp_path / "out.pdf"
    build_pdf([bw, gray], out, ocr=False)
    reader = PdfReader(str(out))
    spaces = []
    for page in reader.pages:
        xobjects = page["/Resources"]["/XObject"]
        for name in xobjects:
            spaces.append(xobjects[name].get_object()["/ColorSpace"])
    assert "/DeviceRGB" not in spaces
```

`tests/test_cli.py` (add; follow the file's existing `patch("scanix500.cli.PySaneDevice")` / `patch("scanix500.cli.capture_pages", ...)` style and helpers):

```python
def test_cli_passes_scan_settings_to_the_device(tmp_path):
    pages = [PagePair(_content_image(), None)]
    with patch("scanix500.cli.PySaneDevice") as device_cls, patch(
        "scanix500.cli.capture_pages", return_value=pages
    ) as capture, patch("scanix500.cli.build_pdf"):
        main([str(tmp_path), "--simplex", "--mode", "gray", "--resolution", "600", "--skip-ocr"])
    device_cls.assert_called_once_with(duplex=False, color_mode="gray", resolution=600)
    assert capture.call_args.kwargs == {"duplex": False}


def test_cli_bw_thresholds_pages_before_building_the_pdf(tmp_path):
    pages = [PagePair(_content_image(), None)]
    with patch("scanix500.cli.PySaneDevice"), patch(
        "scanix500.cli.capture_pages", return_value=pages
    ), patch("scanix500.cli.build_pdf") as build:
        main([str(tmp_path), "--simplex", "--mode", "bw", "--skip-ocr", "--skip-blank-filter"])
    images = build.call_args.args[0]
    assert {img.mode for img in images} == {"1"}


def test_cli_keep_blank_pages_reaches_the_filter(tmp_path):
    pages = [PagePair(_content_image(), None)]
    with patch("scanix500.cli.PySaneDevice"), patch(
        "scanix500.cli.capture_pages", return_value=pages
    ), patch("scanix500.cli.filter_pages", return_value=[[pages[0].front]]) as filt, patch("scanix500.cli.build_pdf"):
        main([str(tmp_path), "--split-on-blank", "--keep-blank-pages", "--skip-ocr"])
    assert filt.call_args.kwargs == {"split_on_blank": True, "drop_blank_pages": False}
```

(`_content_image()` = whatever non-blank image helper `test_cli.py` already uses; create one if none exists: a white `RGB` image with a black rectangle and `info["dpi"] = (300, 300)`.)

`tests/menubar/test_runner.py` (add):

```python
def test_build_argv_adds_only_non_default_settings():
    from scanix500.menubar.runner import _build_argv
    default = _build_argv(Profile(name="p", destination="/d"))
    assert "--simplex" not in default and "--mode" not in default and "--resolution" not in default
    argv = _build_argv(Profile(name="p", destination="/d", duplex=False, color_mode="bw",
                               resolution=600, keep_blank_pages=True))
    assert argv[1:] == ["/d", "--simplex", "--mode", "bw", "--resolution", "600", "--keep-blank-pages"]
```

`tests/menubar/test_profiles.py` (add):

```python
def test_profiles_saved_before_the_new_fields_still_load(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text('[{"name": "Old", "destination": "/d", "skip_blank_filter": false, "skip_ocr": false, "split_on_blank": false}]')
    (tmp_path / ".hardware_button_seeded").touch()
    [profile] = load_profiles(path)
    assert (profile.duplex, profile.color_mode, profile.resolution, profile.keep_blank_pages) == (True, "color", 300, False)
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/test_capture.py tests/test_blank_filter.py tests/test_bw.py tests/test_pdf_builder.py tests/test_cli.py tests/menubar/test_runner.py tests/menubar/test_profiles.py`
Expected: FAIL (unknown keyword `duplex`, missing module `scanix500.bw`, unknown CLI flags, …).

- [ ] **Step 3: Implement**

`src/scanix500/bw.py`:

```python
"""Black & white output. The iX500 scans black & white as gray: the
background trim and the blank-page check need gray levels (in 1-bit the
scanner's background and a white margin look the same), so pages are
thresholded only after both have run."""

from __future__ import annotations

from PIL import Image

# Paper scans at about 225-245, printed text far below 128; 160 keeps light
# pencil and faint print readable. Checked by hand against real scans.
BW_THRESHOLD = 160


def to_black_and_white(image: Image.Image) -> Image.Image:
    out = image.convert("L").point(lambda v: 255 if v >= BW_THRESHOLD else 0, mode="1")
    out.info["dpi"] = image.info.get("dpi", (300, 300))
    return out
```

`src/scanix500/capture.py`:
- `PagePair.back: Image.Image | None = None`.
- Add after `DEFAULT_MODE`:

```python
# Dossiary's colour modes to SANE's. Black & white is scanned as gray and
# thresholded later (see bw.py).
SANE_MODES = {"color": "Color", "gray": "Gray", "bw": "Gray"}
```

- `capture_pages(device: SaneDevice, *, duplex: bool = True)`: unchanged for duplex; when `duplex` is False each `read_frame()` is one sheet, `PagePair(front, None)`, with the same jam handling (an exception from `read_frame` → `MultiFeedError(sheet_index, pages)`), the same `multi_feed_detected()` check after each sheet, and `NoPagesScannedError` when nothing came back:

```python
def capture_pages(device: SaneDevice, *, duplex: bool = True) -> list[PagePair]:
    device.start()
    pages: list[PagePair] = []
    sheet_index = 0
    while True:
        try:
            front = device.read_frame()
        except StopIteration:
            break
        except Exception:
            # (keep the existing comment about real jams here)
            raise MultiFeedError(sheet_index, pages)
        back = None
        if duplex:
            try:
                back = device.read_frame()
            except StopIteration:
                # (keep the existing odd-frame-count comment here)
                break
            except Exception:
                raise MultiFeedError(sheet_index, pages)
        sheet_index += 1
        pages.append(PagePair(front, back))
        if device.multi_feed_detected():
            raise MultiFeedError(sheet_index, pages)

    if not pages:
        raise NoPagesScannedError()
    return pages
```

- `PySaneDevice.__init__(self, *, duplex: bool = True, color_mode: str = "color", resolution: int = DEFAULT_RESOLUTION_DPI)`: `self._dev.source = "ADF Duplex" if duplex else "ADF Front"`, `self._dev.resolution = resolution`, `self._dev.mode = SANE_MODES[color_mode]`. Keep every existing comment (the Lineart one now explains why the default is Color); everything else unchanged.

`src/scanix500/blank_filter.py` — replace `filter_pages`:

```python
def filter_pages(
    pages: list[PagePair],
    *,
    threshold: float = 0.02,
    split_on_blank: bool = False,
    drop_blank_pages: bool = True,
) -> list[list[Image.Image]]:
    """Groups the pages into documents. A sheet is blank when every side
    it has is blank (a front-only sheet has no back). With split_on_blank a
    blank sheet ends the current document and is dropped. With
    drop_blank_pages (the default) every blank page is dropped; without it,
    blank pages are kept, except a blank sheet used as a separator."""
    partitions: list[list[Image.Image]] = [[]]
    for pair in pages:
        sides = [pair.front] if pair.back is None else [pair.front, pair.back]
        blank = [is_blank(img, threshold) for img in sides]

        if all(blank) and split_on_blank:
            if partitions[-1]:
                partitions.append([])
            continue

        for img, img_blank in zip(sides, blank):
            if not (img_blank and drop_blank_pages):
                partitions[-1].append(img)

    return [p for p in partitions if p]
```

`src/scanix500/pdf_builder.py` — in `_save_image_only_pdf`, keep 1-bit and gray pages as they are:

```python
    page = image if image.mode in ("1", "L") else image.convert("RGB")
    page.save(path, format="PDF", resolution=dpi)
```

`src/scanix500/cli.py`:

```python
    parser.add_argument("--simplex", action="store_true", help="scan the front of each sheet only")
    parser.add_argument("--mode", choices=("color", "gray", "bw"), default="color")
    parser.add_argument("--resolution", type=int, default=300)
    parser.add_argument("--keep-blank-pages", action="store_true",
                        help="with --split-on-blank: keep blank pages, drop only blank separator sheets")
```

In `main`: `device = PySaneDevice(duplex=not args.simplex, color_mode=args.mode, resolution=args.resolution)`, `pages = capture_pages(device, duplex=not args.simplex)`; trim with `PagePair(trim_background(p.front), None if p.back is None else trim_background(p.back))`; the `--skip-blank-filter` partition skips `None` backs; otherwise `filter_pages(pages, split_on_blank=args.split_on_blank, drop_blank_pages=not args.keep_blank_pages)`; after the "all blank" check and before building PDFs:

```python
    if args.mode == "bw":
        partitions = [[to_black_and_white(img) for img in images] for images in partitions]
```

`src/scanix500/menubar/profiles.py` — `Profile` gains, after `split_on_blank`:

```python
    duplex: bool = True
    color_mode: str = "color"
    resolution: int = 300
    keep_blank_pages: bool = False
```

`src/scanix500/menubar/runner.py` — `_build_argv` appends, after the existing flags:

```python
    if not profile.duplex:
        argv.append("--simplex")
    if profile.color_mode != "color":
        argv += ["--mode", profile.color_mode]
    if profile.resolution != 300:
        argv += ["--resolution", str(profile.resolution)]
    if profile.keep_blank_pages:
        argv.append("--keep-blank-pages")
```

Existing tests that call `PySaneDevice()`/`capture_pages(device)` positionally keep working (keyword-only defaults). If an existing test's fake breaks on the new keyword arguments, extend the fake to accept them.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (134 + the new ones).

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "Scan settings: one or both sides, color/gray/black & white, resolution, keep blank pages"
```

---

### Task 2: Version 2 request handling and fake scanners (pure modules)

**Files:**
- Create: `src/scanix500/menubar/protocol_v2.py`, `src/scanix500/menubar/fakes.py`
- Modify: `src/scanix500/__init__.py`, `pyproject.toml`
- Test: create `tests/menubar/test_protocol_v2.py`, `tests/menubar/test_fakes.py`

**Interfaces:**
- Consumes: `Profile` fields from Task 1; `ScanResult` (`runner.py`); `ScanBusyError` and `PairingError` (existing).
- Produces:
  - `scanix500.__version__ = "0.4.0"`.
  - `protocol_v2`: `IX500` (dict), `ProtocolError(status, message)`, `error_body(message) -> dict`, `validate(body, scanners: list[dict]) -> tuple[dict, dict]` (settings keys `source`, `duplex`, `colorMode`, `resolution`, `format`, `extras`), `pages_message(n) -> str`, `profile_for(settings, destination) -> Profile`, `response_from_result(result: ScanResult) -> tuple[int, dict]`, `EMPTY_FEEDER_MESSAGE`, `NOT_CONNECTED_MESSAGE`, `ALL_BLANK_MESSAGE`.
  - `fakes`: `FAKE_PAIRING_CODE`, `FAKE_SCANNERS` (list of dicts), `simulate(scanner, settings)`, `make_pdf(texts) -> bytes`, `FakeBackend` (`scanners()`, `scan(scanner, settings) -> (status, body)`, raises `ScanBusyError` while another fake scan runs), `FakePairing` (`pair(code) -> token`, `is_valid(token) -> bool`).

- [ ] **Step 1: Write the failing tests**

`tests/menubar/test_protocol_v2.py`:

```python
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
    (ScanResult(False, False, "Failed to write output to /x: denied", []), 500, "Failed to write output to /x: denied"),
])
def test_failures(result, status, message):
    got_status, body = response_from_result(result)
    assert (got_status, body) == (status, error_body(message))


def test_error_body_has_both_keys():
    assert error_body("Nope.") == {"ok": False, "partial": False, "message": "Nope.", "error": "Nope."}
```

`tests/menubar/test_fakes.py`:

```python
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
    assert [s["id"] for s in FAKE_SCANNERS] == ["fake:flatbed", "fake:feeder", "fake:splitter", "fake:jam",
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/menubar/test_protocol_v2.py tests/menubar/test_fakes.py`
Expected: FAIL (`ModuleNotFoundError: scanix500.menubar.protocol_v2`).

- [ ] **Step 3: Implement**

`src/scanix500/__init__.py`:

```python
__version__ = "0.4.0"
```

`pyproject.toml`: `version = "0.4.0"`.

`src/scanix500/menubar/protocol_v2.py`:

```python
"""Version 2 of the Dossiary scan bridge protocol (dossiary-scan-helper's
PROTOCOL.md): the iX500's scanner entry, scan request validation, and
turning a scan result into a response. No HTTP and no rumps/AppKit here."""

from __future__ import annotations

import base64
from pathlib import Path

from pypdf import PdfReader

from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult

# Always listed: probing SANE from the HTTP thread would race the menu bar's
# button poller. A scan with the scanner unplugged answers 503 instead.
IX500 = {
    "id": "sane:ix500",
    "name": "ScanSnap iX500",
    "sources": ["feeder"],
    "duplex": True,
    "colorModes": ["color", "gray", "bw"],
    "resolutions": [300, 600],
    "extras": ["splitOnBlank", "skipBlankPages"],
}

EMPTY_FEEDER_MESSAGE = "No paper in the feeder."
ALL_BLANK_MESSAGE = "Every page was blank, so no PDF was made."
NOT_CONNECTED_MESSAGE = "The ScanSnap iX500 isn't connected. Check that it's plugged in and switched on."


class ProtocolError(Exception):
    """A request the protocol refuses: HTTP status plus a message for a person."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def error_body(message: str) -> dict:
    # "error" repeats the message for Dossiary 1.43, which reads that key.
    return {"ok": False, "partial": False, "message": message, "error": message}


def validate(body: object, scanners: list[dict]) -> tuple[dict, dict]:
    """PROTOCOL.md "Request validation" steps 1-5. Returns the scanner and
    the settings with every default filled in."""
    if not isinstance(body, dict):
        raise ProtocolError(400, "The scan request must be a JSON object.")
    scanner_id = body.get("scanner")
    if not isinstance(scanner_id, str):
        raise ProtocolError(400, "The scan request needs a scanner id.")
    scanner = next((s for s in scanners if s["id"] == scanner_id), None)
    if scanner is None:
        raise ProtocolError(404, f"No scanner with id “{scanner_id}”.")

    for key in ("source", "colorMode", "format"):
        if key in body and not isinstance(body[key], str):
            raise ProtocolError(400, f"“{key}” must be text.")
    if "resolution" in body and (not isinstance(body["resolution"], int) or isinstance(body["resolution"], bool)):
        raise ProtocolError(400, "“resolution” must be a whole number.")
    if "duplex" in body and not isinstance(body["duplex"], bool):
        raise ProtocolError(400, "“duplex” must be true or false.")
    extras = body.get("extras", {})
    if not isinstance(extras, dict) or not all(isinstance(v, bool) for v in extras.values()):
        raise ProtocolError(400, "“extras” must map names to true or false.")

    name = scanner["name"]
    source = body.get("source", scanner["sources"][0])
    if source not in scanner["sources"]:
        raise ProtocolError(422, f"{name} has no {source} source.")
    color_mode = body.get("colorMode", scanner["colorModes"][0])
    if color_mode not in scanner["colorModes"]:
        raise ProtocolError(422, f"{name} can't scan in {color_mode}.")
    resolution = body.get("resolution", scanner["resolutions"][0])
    if resolution not in scanner["resolutions"]:
        raise ProtocolError(422, f"{name} can't scan at {resolution} dpi.")
    fmt = body.get("format", "pdf")
    if fmt != "pdf":
        raise ProtocolError(422, f"Format “{fmt}” isn't supported; use pdf.")
    duplex = body.get("duplex", False)
    if duplex and not (scanner["duplex"] and source == "feeder"):
        raise ProtocolError(422, f"{name} can't scan both sides from the {source}.")
    for extra, on in extras.items():
        if on and extra not in scanner["extras"]:
            raise ProtocolError(422, f"{name} doesn't offer “{extra}”.")

    return scanner, {"source": source, "duplex": duplex, "colorMode": color_mode,
                     "resolution": resolution, "format": fmt, "extras": dict(extras)}


def pages_message(count: int) -> str:
    return "1 page scanned." if count == 1 else f"{count} pages scanned."


def profile_for(settings: dict, destination: str) -> Profile:
    """The one-off Profile a version 2 request runs. OCR stays on, as for
    every scanix500 scan. No extras means no blank filtering at all."""
    extras = settings["extras"]
    split = extras.get("splitOnBlank", False)
    skip_blank = extras.get("skipBlankPages", False)
    return Profile(
        name="Dossiary Bridge Scan",
        destination=destination,
        skip_blank_filter=not (split or skip_blank),
        skip_ocr=False,
        split_on_blank=split,
        duplex=settings["duplex"],
        color_mode=settings["colorMode"],
        resolution=settings["resolution"],
        keep_blank_pages=split and not skip_blank,
    )


def _file_entry(path: str) -> dict | None:
    try:
        data = Path(path).read_bytes()
        pages = len(PdfReader(path).pages)
    except Exception:  # noqa: BLE001 - an unreadable file is left out, not fatal
        return None
    return {"name": Path(path).name, "pages": pages, "data": base64.b64encode(data).decode("ascii")}


def _first_part(message: str) -> str:
    # runner.py appends " — partial scan saved to <path>"; the path means
    # nothing to Dossiary.
    return message.split(" — ")[0].strip()


def response_from_result(result: ScanResult) -> tuple[int, dict]:
    files = [entry for entry in (_file_entry(p) for p in result.output_paths) if entry]
    if result.ok:
        if files:
            total = sum(f["pages"] for f in files)
            return 200, {"ok": True, "partial": False, "message": pages_message(total), "files": files}
        if result.message == "No pages scanned.":
            return 500, error_body(EMPTY_FEEDER_MESSAGE)
        if result.message.startswith("All pages were blank"):
            return 500, error_body(ALL_BLANK_MESSAGE)
        return 500, error_body("The scan finished, but its PDF couldn't be read.")
    if result.partial and files:
        return 200, {"ok": False, "partial": True, "message": _first_part(result.message), "files": files}
    if result.message.startswith("Scanner not found"):
        return 503, error_body(NOT_CONNECTED_MESSAGE)
    return 500, error_body(_first_part(result.message) or "The scan failed.")
```

`src/scanix500/menubar/fakes.py` (port of dossiary-scan-helper's `reference/scanbridge_ref/fakes.py` and `pdf.py`; read those files for reference — behaviour must match PROTOCOL.md "Fake scanner mode"):

```python
"""The protocol's fake scanner mode (PROTOCOL.md "Fake scanner mode"), so
dossiary-scan-helper's conformance suite can test this bridge without an
iX500. Ported from that repo's reference helper (MIT). Testing only."""

from __future__ import annotations

import base64
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from scanix500.menubar.bridge import ScanBusyError
from scanix500.menubar.pairing import PairingError
from scanix500.menubar.protocol_v2 import EMPTY_FEEDER_MESSAGE, ProtocolError, error_body, pages_message

FAKE_PAIRING_CODE = "000000"


def _entry(id: str, name: str, sources: list, duplex: bool = False, extras: tuple = ()) -> dict:
    return {"id": id, "name": name, "sources": sources, "duplex": duplex,
            "colorModes": ["color", "gray", "bw"], "resolutions": [150, 300, 600], "extras": list(extras)}


FAKE_SCANNERS = [
    _entry("fake:flatbed", "Fake Flatbed", ["flatbed"]),
    _entry("fake:feeder", "Fake Feeder", ["feeder", "flatbed"], duplex=True),
    _entry("fake:splitter", "Fake Splitter", ["feeder"], extras=("splitOnBlank", "skipBlankPages")),
    _entry("fake:jam", "Fake Jamming Feeder", ["feeder"]),
    _entry("fake:slow", "Fake Slow Scanner", ["flatbed"]),
    _entry("fake:offline", "Fake Offline Scanner", ["flatbed"]),
    _entry("fake:empty", "Fake Empty Feeder", ["feeder"]),
]


@dataclass(frozen=True)
class _Behaviour:
    sheets: tuple = ()
    delay: float = 0.0
    offline: bool = False
    jam_after: int | None = None
    empty: bool = False


_BEHAVIOUR = {
    "fake:feeder": _Behaviour(sheets=("Sheet 1", "Sheet 2", "Sheet 3")),
    "fake:splitter": _Behaviour(sheets=("Doc A page 1", "Doc A page 2", "", "Doc B page 1")),
    "fake:jam": _Behaviour(sheets=("Sheet 1", "Sheet 2", "Sheet 3"), jam_after=2),
    "fake:slow": _Behaviour(delay=2.0),
    "fake:offline": _Behaviour(offline=True),
    "fake:empty": _Behaviour(empty=True),
}


def simulate(scanner: dict, settings: dict) -> tuple[list[list[str]], bool, str]:
    """Runs a fake scan: (documents as lists of page texts, partial, message)."""
    b = _BEHAVIOUR.get(scanner["id"], _Behaviour())
    if b.offline:
        raise ProtocolError(503, f"{scanner['name']} isn't reachable. Check that it's switched on and connected.")
    if b.delay:
        time.sleep(b.delay)
    if b.empty:
        raise ProtocolError(500, EMPTY_FEEDER_MESSAGE)

    if settings["source"] == "flatbed":
        pages = ["Flatbed page 1"]
    else:
        sheets = b.sheets if b.jam_after is None else b.sheets[: b.jam_after]
        pages = []
        for sheet in sheets:
            pages.append(sheet)
            if settings["duplex"]:
                pages.append(f"{sheet} back" if sheet else "")

    extras = settings["extras"]
    if extras.get("splitOnBlank"):
        documents, current = [], []
        for page in pages:
            if page == "":
                if current:
                    documents.append(current)
                current = []
            else:
                current.append(page)
        if current:
            documents.append(current)
    else:
        if extras.get("skipBlankPages"):
            pages = [p for p in pages if p != ""]
        documents = [pages]

    if b.jam_after is not None and settings["source"] == "feeder":
        return documents, True, f"Paper jam after {b.jam_after} sheets. The pages before it were kept."
    return documents, False, pages_message(sum(len(d) for d in documents))


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(page_texts: list[str]) -> bytes:
    """A PDF with one A4 page per text ("" gives a blank page)."""
    if not page_texts:
        raise ValueError("a PDF needs at least one page")
    count = len(page_texts)
    # Object numbers: 1 catalog, 2 page tree, 3 font, then a page and its content per text.
    page_numbers = [4 + 2 * i for i in range(count)]
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(f"{n} 0 R" for n in page_numbers), count)).encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_number, text in zip(page_numbers, page_texts):
        bodies.append((
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_number + 1} 0 R >>"
        ).encode())
        stream = f"BT /F1 36 Tf 72 720 Td ({_escape(text)}) Tj ET".encode("latin-1")
        bodies.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


class FakeBackend:
    def __init__(self):
        self._lock = threading.Lock()

    def scanners(self) -> list[dict]:
        return [dict(s) for s in FAKE_SCANNERS]

    def scan(self, scanner: dict, settings: dict) -> tuple[int, dict]:
        if not self._lock.acquire(blocking=False):
            raise ScanBusyError()
        try:
            documents, partial, message = simulate(scanner, settings)
        except ProtocolError as e:
            return e.status, error_body(e.message)
        finally:
            self._lock.release()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        files = []
        for i, pages in enumerate(documents, start=1):
            name = f"scan_{stamp}.pdf" if len(documents) == 1 else f"scan_{stamp}_{i}.pdf"
            files.append({"name": name, "pages": len(pages),
                          "data": base64.b64encode(make_pdf(pages)).decode("ascii")})
        return 200, {"ok": not partial, "partial": partial, "message": message, "files": files}


class FakePairing:
    """Fake scanner mode's pairing: the code is always 000000, any number
    of times, never expiring (PROTOCOL.md "Pairing"). Tokens live in memory."""

    def __init__(self):
        self._tokens: set[str] = set()
        self._lock = threading.Lock()

    def pair(self, code: str) -> str:
        if code != FAKE_PAIRING_CODE:
            raise PairingError("That code isn't right. Choose “Pair a browser…” in the helper and enter the new code.")
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._tokens.add(token)
        return token

    def is_valid(self, token: str | None) -> bool:
        with self._lock:
            return bool(token) and token in self._tokens
```



`fakes.py` imports `ScanBusyError` from `bridge.py`; `bridge.py` must never import `fakes.py` (only `bridge_main.py` does, Task 4).

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src tests pyproject.toml
git commit -m "Protocol version 2: request validation, responses, and the fake scanners"
```

---

### Task 3: Bridge routing for both protocol versions

**Files:**
- Modify: `src/scanix500/menubar/bridge.py`
- Test: `tests/menubar/test_bridge.py`

**Interfaces:**
- Consumes: Task 2's `protocol_v2` and `__version__`; `PairingStore` / `FakePairing` (both have `pair(code)` and `is_valid(token)`).
- Produces: `V2Backend` protocol (`scanners() -> list[dict]`, `scan(scanner, settings) -> tuple[int, dict]`, may raise `ScanBusyError`); `RealBackend(get_destination, trigger)`; `make_handler_class(get_destination, trigger, pairing, backend)`; `start_bridge_server(get_destination, trigger, pairing, port=None, backend=None)` — `backend=None` means `RealBackend(get_destination, trigger)`; `trigger=None` turns the version 1 request off (fake mode). `MAX_BODY_BYTES = 1_000_000`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/menubar/test_bridge.py` (reuse its `unused_tcp_port`, `PAIRING`, `TOKEN`, `FakeScanTrigger`):

```python
def _request(base, method, path, body=None, token=TOKEN, headers=None):
    all_headers = dict(headers or {})
    if token:
        all_headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(base + path, method=method, data=data, headers=all_headers)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None), dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None), dict(e.headers)


@pytest.fixture
def v2_server(unused_tcp_port, tmp_path):
    trigger = FakeScanTrigger(result=ScanResult(ok=True, partial=False, message="No pages scanned.", output_paths=[]))
    server = start_bridge_server(lambda: str(tmp_path), trigger, PAIRING, port=unused_tcp_port)
    yield f"http://127.0.0.1:{unused_tcp_port}", trigger
    server.shutdown()
    server.server_close()


def test_health_is_version_2_and_keeps_the_service_name(v2_server):
    base, _ = v2_server
    status, body, headers = _request(base, "GET", "/health")
    assert status == 200
    assert body == {"ok": True, "helper": "scanix500", "version": "0.4.0", "protocol": 2,
                    "paired": True, "service": "scanix500-bridge"}
    assert headers["Content-Type"].startswith("application/json")


def test_scanners_lists_the_ix500(v2_server):
    base, _ = v2_server
    status, body, _ = _request(base, "GET", "/scanners")
    assert status == 200 and [s["id"] for s in body["scanners"]] == ["sane:ix500"]


def test_scanners_needs_a_token(v2_server):
    base, _ = v2_server
    status, body, _ = _request(base, "GET", "/scanners", token=None)
    assert status == 401 and body["ok"] is False and body["message"]


def test_v2_scan_runs_the_profile_from_the_settings(v2_server):
    base, trigger = v2_server
    status, body, _ = _request(base, "POST", "/scan", {"scanner": "sane:ix500", "duplex": True,
                                                     "colorMode": "gray", "resolution": 600,
                                                     "extras": {"splitOnBlank": True}})
    assert (status, body["message"]) == (500, "No paper in the feeder.")
    p = trigger.called_with
    assert (p.duplex, p.color_mode, p.resolution, p.split_on_blank, p.keep_blank_pages, p.skip_ocr) == (
        True, "gray", 600, True, True, False)


def test_v2_scan_busy_is_409(unused_tcp_port):
    server = start_bridge_server(lambda: "/tmp", FakeScanTrigger(raises=ScanBusyError()), PAIRING, port=unused_tcp_port)
    try:
        status, body, _ = _request(f"http://127.0.0.1:{unused_tcp_port}", "POST", "/scan", {"scanner": "sane:ix500"})
        assert status == 409 and body["ok"] is False
    finally:
        server.shutdown(); server.server_close()


@pytest.mark.parametrize("method, path, allow", [
    ("GET", "/scan", "POST, OPTIONS"), ("POST", "/health", "GET, OPTIONS"),
    ("PUT", "/pair", "POST, OPTIONS"), ("DELETE", "/scanners", "GET, OPTIONS"),
])
def test_wrong_method_is_405_with_allow(v2_server, method, path, allow):
    base, _ = v2_server
    status, body, headers = _request(base, method, path, body=b"{}" if method != "GET" else None, token=None)
    assert status == 405 and headers["Allow"] == allow and body["ok"] is False


def test_unknown_path_is_404_for_any_method(v2_server):
    base, _ = v2_server
    for method in ("GET", "POST", "DELETE"):
        status, body, _ = _request(base, method, "/nope", body=b"{}" if method == "POST" else None)
        assert status == 404 and body["ok"] is False


def test_v2_bad_bodies_are_400(v2_server):
    base, _ = v2_server
    for raw in (b"", b"not json", b"[]", b"null"):
        status, body, _ = _request(base, "POST", "/scan", raw)
        assert status == 400 and body["ok"] is False


def test_pair_validates_client(unused_tcp_port, tmp_path):
    store = PairingStore(tmp_path / "p.json")
    server = start_bridge_server(lambda: "/tmp", FakeScanTrigger(), store, port=unused_tcp_port)
    base = f"http://127.0.0.1:{unused_tcp_port}"
    try:
        code = store.open_window()
        assert _request(base, "POST", "/pair", {"code": code, "client": 5}, token=None)[0] == 400
        assert _request(base, "POST", "/pair", {"code": code, "client": "x" * 201}, token=None)[0] == 400
        status, body, _ = _request(base, "POST", "/pair", {"code": code, "client": "Chrome"}, token=None)
        assert status == 200 and body["ok"] is True and store.is_valid(body["token"])
        status, body, _ = _request(base, "POST", "/pair", {"code": "000000"}, token=None)
        assert status == 403 and body["ok"] is False and body["error"] == body["message"]
    finally:
        server.shutdown(); server.server_close()


def test_version_1_request_still_works(v2_server, tmp_path):
    base, trigger = v2_server
    status, body = _post(_scan_url(base))
    assert status == 200 and "output_paths" in body and trigger.called_with.name == "Dossiary Bridge Scan"
```

Existing tests that pinned behaviour the protocol now defines differently (e.g. `GET /scan` or an unhandled method answering 501 with an HTML body, `/health`'s exact old body, the old 404 body `{"error": "not found"}`) are updated to the protocol's behaviour; keep every test about origins, CORS headers, the preflight, the version 1 request, its 401 and pairing.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/menubar/test_bridge.py`
Expected: the new tests FAIL (no `/scanners`, old health body, 501 for PUT…).

- [ ] **Step 3: Implement**

In `src/scanix500/menubar/bridge.py` keep `DEFAULT_PORT`, `origin_allowed`, `ScanBusyError`, `ScanTrigger`, `_encode_files`, `_parse_bool_param`, `route_scan_request` unchanged. Add/replace:

```python
from scanix500 import __version__
from scanix500.menubar.protocol_v2 import IX500, ProtocolError, error_body, profile_for, response_from_result, validate

MAX_BODY_BYTES = 1_000_000
JSON_TYPE = "application/json; charset=utf-8"
# Each endpoint's one method (PROTOCOL.md "Endpoints and methods").
ENDPOINTS = {"/health": "GET", "/scanners": "GET", "/scan": "POST", "/pair": "POST"}
NOT_PAIRED_MESSAGE = (
    "This browser isn't paired with scanix500. Choose “Pair a Browser…” in the scanix500 menu "
    "and enter the code in Dossiary."
)


class V2Backend(Protocol):
    """Where version 2 scanners come from: the real iX500 (RealBackend) or
    the protocol's fake scanners (fakes.FakeBackend)."""

    def scanners(self) -> list[dict]: ...
    def scan(self, scanner: dict, settings: dict) -> tuple[int, dict]: ...


class RealBackend:
    def __init__(self, get_destination: Callable[[], str], trigger: ScanTrigger):
        self._get_destination = get_destination
        self._trigger = trigger

    def scanners(self) -> list[dict]:
        return [dict(IX500)]

    def scan(self, scanner: dict, settings: dict) -> tuple[int, dict]:
        # ScanBusyError propagates to the handler (409).
        result = self._trigger.trigger(profile_for(settings, self._get_destination()))
        return response_from_result(result)


class _BodyError(ValueError):
    pass
```

`make_handler_class(get_destination, trigger, pairing, backend)` — one `_dispatch()` for every method, in the protocol's order:

```python
    class Handler(BaseHTTPRequestHandler):
        # (keep _cors_headers and send_error as they are)

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

        def _preflight(self) -> None:
            # (the body of today's do_OPTIONS after the origin check, unchanged)

        def _dispatch(self) -> None:
            if not self._origin_ok():
                return
            if self.command == "OPTIONS":
                self._preflight()
                return
            url = urlsplit(self.path)
            method = ENDPOINTS.get(url.path)
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
            self._handle_v2_scan()

        def _handle_v1_scan(self, query: str, token_ok: bool) -> None:
            # Version 1 (Dossiary before 1.44): query parameters, {"error"} bodies.
            if not token_ok:
                self._send_json(401, {"error": "this browser isn't paired; choose “Pair a Browser…” in the scanix500 menu"})
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

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own naming
            self._dispatch()

        do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_GET  # noqa: N815

        # (keep log_message as it is)
```

`start_bridge_server(get_destination, trigger, pairing, port=None, backend=None)`: `backend = backend or RealBackend(get_destination, trigger)` and pass it to `make_handler_class`. Update the docstrings that describe the old routes (module/handler/do_POST comments about `/scan` never reading the body: version 2 does read it; the connection still closes after each response under HTTP/1.0).

`app.py` needs no change: it calls `start_bridge_server(lambda: self.bridge_destination, self, self.pairing)`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "Bridge: protocol version 2 endpoints, keeping the version 1 scan request"
```

---

### Task 4: Fake-mode bridge, conformance run, docs

**Files:**
- Create: `src/scanix500/menubar/bridge_main.py`, `scripts/run-conformance.sh` (executable)
- Modify: `pyproject.toml` (console script), `README.md`, `CLAUDE.md`
- Test: create `tests/menubar/test_bridge_main.py`

**Interfaces:**
- Consumes: `start_bridge_server(..., backend=)`, `FakeBackend`, `FakePairing`.
- Produces: console script `scanix500-bridge --fake-scanners [--port N]`.

- [ ] **Step 1: Write the failing test**

`tests/menubar/test_bridge_main.py`:

```python
import pytest

from scanix500.menubar.bridge_main import parse_args


def test_fake_scanners_flag_and_port():
    args = parse_args(["--fake-scanners", "--port", "8799"])
    assert args.fake_scanners and args.port == 8799


def test_real_mode_is_refused():
    with pytest.raises(SystemExit):
        parse_args([])
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest -q tests/menubar/test_bridge_main.py`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`src/scanix500/menubar/bridge_main.py`:

```python
"""`scanix500-bridge --fake-scanners`: the HTTP bridge on its own, with the
protocol's fake scanners and pairing code 000000, for running
dossiary-scan-helper's conformance suite. Testing only -- the real bridge
runs inside scanix500-menubar."""

from __future__ import annotations

import argparse
import threading

from scanix500.menubar.bridge import DEFAULT_PORT, start_bridge_server
from scanix500.menubar.fakes import FakeBackend, FakePairing


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="scanix500-bridge")
    parser.add_argument("--fake-scanners", action="store_true", help="serve the protocol's fake scanners")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if not args.fake_scanners:
        parser.error("only --fake-scanners is supported; the real bridge runs inside scanix500-menubar")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    server = start_bridge_server(lambda: "", None, FakePairing(), port=args.port, backend=FakeBackend())
    print(f"scanix500 bridge with fake scanners on http://127.0.0.1:{args.port}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0
```

`pyproject.toml` `[project.scripts]`: add `scanix500-bridge = "scanix500.menubar.bridge_main:main"`. Then reinstall so the script exists: `.venv/bin/pip install -e . -q`.

`scripts/run-conformance.sh`:

```bash
#!/bin/bash
# Runs dossiary-scan-helper's conformance suite against this bridge in fake
# scanner mode. Usage: scripts/run-conformance.sh [path to dossiary-scan-helper]
set -euo pipefail
cd "$(dirname "$0")/.."
SUITE_REPO="${1:-../Paperless/dossiary-scan-helper}"
PORT="${PORT:-8798}"
[ -d "$SUITE_REPO/conformance" ] || { echo "No conformance suite in $SUITE_REPO"; exit 1; }
.venv/bin/scanix500-bridge --fake-scanners --port "$PORT" > /tmp/scanix500-conformance.log 2>&1 &
PID=$!
trap 'kill "$PID" 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
  if curl -sf "http://127.0.0.1:$PORT/health" > /dev/null; then break; fi
  sleep 0.2
done
curl -sf "http://127.0.0.1:$PORT/health" > /dev/null || { echo "bridge did not start"; cat /tmp/scanix500-conformance.log; exit 1; }
cd "$SUITE_REPO"
SCAN_HELPER_URL="http://127.0.0.1:$PORT" python3 -m unittest discover -s conformance -v
```

Docs:
- `README.md`: a short "Protocol version 2 (0.4.0)" section: Dossiary 1.44+ shows the iX500 in its scan dialog with one or both sides, color / gray / black & white, 300 or 600 dpi, split on blank pages and skip blank pages; OCR still runs; older Dossiary releases keep using version 1; `scripts/run-conformance.sh` runs dossiary-scan-helper's conformance suite against `scanix500-bridge --fake-scanners`. Update the existing bridge/API description and the Status section where they say the bridge has only `/health`, `/pair` and `/scan`.
- `CLAUDE.md`: a "Protocol version 2 (0.4.0)" bullet under "Key constraints" in the existing style: `protocol_v2.py` (pure: the always-listed iX500 entry and why it isn't probed, validation, response mapping including the `error` key), `bridge.py`'s check order and how a version 1 request is recognized (query string on `POST /scan`), black & white scanned as gray then thresholded (`bw.py`), `keep_blank_pages` for split-only, `fakes.py`/`bridge_main.py` for conformance. Add the plan to the spec/plan list at the top.

- [ ] **Step 4: Verify**

Run: `.venv/bin/python -m pytest -q` → all pass.
Run: `scripts/run-conformance.sh` → `Ran 43 tests … OK` (if a test fails, fix the bridge, not the suite).

- [ ] **Step 5: Commit**

```bash
git add src tests pyproject.toml scripts README.md CLAUDE.md
git commit -m "scanix500-bridge --fake-scanners and a conformance run; docs for protocol version 2"
```

---

### Task 5: Hardware check and Dossiary docs (owner present)

- [ ] **Step 1: Restart the menu bar app on the new code:** `./stop-menubar.sh && ./start-menubar.sh`; `curl -s http://127.0.0.1:8765/health` shows `"protocol": 2, "version": "0.4.0"`.
- [ ] **Step 2: In Dossiary (v1.44+), 📷 Scan:** "ScanSnap iX500" is listed with Document feeder, Both sides, Color/Gray/Black and white, 300/600 dpi, Split on blank pages and Skip blank pages. 📸 Scan Multi is visible.
- [ ] **Step 3: Scans (each lands in the Inbox):**
  1. One side, color, 300 dpi, 2 sheets → 2 pages.
  2. Both sides, gray, 300 dpi, 2 sheets with blank backs, Skip blank pages → 2 pages; again without Skip blank pages → 4 pages.
  3. Black and white, 600 dpi → crisp 1-bit pages, margins not cut off, text and light print readable, PDF searchable.
  4. Scan Multi (Split on blank pages) with a blank separator sheet → 2 documents.
  5. Empty feeder → "No paper in the feeder."
  6. Scanner unplugged → "The ScanSnap iX500 isn't connected…".
  7. The hardware Scan button and the menu's profiles still scan as before (duplex, color, 300 dpi).
- [ ] **Step 4: Dossiary docs** (in `/Users/aarneaarebye/Projects/Paperless/Dossiary`): in `CLAUDE.md`'s "Scanning: the scan dialog and its helpers" note, say scanix500 0.4.0+ answers version 2 (the iX500's real settings) and `V1_SCANNER` covers only older scanix500; in the spec's "4. Order of work", mark step 4 done with the date and this plan's path. Commit there.
