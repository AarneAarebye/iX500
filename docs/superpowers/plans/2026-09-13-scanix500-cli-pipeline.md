# scanix500 CLI Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `scanix500` CLI — a vendor-independent replacement for ScanSnap Home's day-to-day scanning, driving the iX500 directly via SANE.

**Architecture:** A capture module drives libsane directly (via `python-sane`) to get correct ADF-duplex behavior that `scanimage`/`scanadf` mishandle. A blank-page filter drops blank sides and optionally splits the batch on fully-blank separator sheets. A PDF builder OCRs each page independently (so one bad page can't sink the batch) and merges them. A router computes timestamped output paths. The CLI wires these together behind argparse flags.

**Tech Stack:** Python 3.11+, Pillow (image handling), pypdf (PDF merge), `python-sane` (SANE bindings, hardware-only), `ocrmypdf` CLI (external tool, invoked via subprocess), pytest.

## Global Constraints

- No Ricoh/ScanSnap account, cloud service, or vendor software dependency anywhere in this pipeline.
- ADF Duplex capture must go through `python-sane` directly, not `scanimage`/`scanadf` (those tools mishandle the iX500's dual image-stream output — see spec's "Known technical constraint").
- Default CLI behavior (no flags) must reproduce ScanSnap Home's day-to-day output: duplex capture, blank-page removal, OCR, single PDF. `--skip-blank-filter`, `--skip-ocr`, `--split-on-blank` are opt-outs/opt-ins layered on that default.
- Multi-feed/jam detection must stop the batch but preserve pages already captured — never silently produce a corrupted document, never discard partial work.
- OCR failure on one page must not fail the whole batch; that page falls back to image-only.
- Hardware-touching code (real SANE device I/O) is verified via the manual checklist in Task 8, not mocked in automated tests — automated tests cover pure orchestration logic against fakes/doubles.

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/scanix500/__init__.py`
- Create: `src/scanix500/errors.py`
- Create: `tests/__init__.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Produces: `scanix500.errors.ScannerNotFoundError`, `scanix500.errors.MultiFeedError(sheet_index: int, pages_captured: list)`, `scanix500.errors.NoPagesScannedError` — used by Tasks 2 and 7.

- [ ] **Step 1: Create the package skeleton and pyproject.toml**

```toml
# pyproject.toml
[project]
name = "scanix500"
version = "0.1.0"
description = "Vendor-independent scan pipeline for the ScanSnap iX500"
requires-python = ">=3.11"
dependencies = [
    "Pillow>=10.0",
    "pypdf>=4.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
scanix500 = "scanix500.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/scanix500"]
```

Create empty `src/scanix500/__init__.py` and empty `tests/__init__.py`.

- [ ] **Step 2: Write the failing test for errors.py**

```python
# tests/test_errors.py
from scanix500.errors import MultiFeedError, NoPagesScannedError, ScannerNotFoundError


def test_multi_feed_error_carries_sheet_index_and_pages():
    err = MultiFeedError(sheet_index=3, pages_captured=["a", "b"])
    assert err.sheet_index == 3
    assert err.pages_captured == ["a", "b"]
    assert "3" in str(err)


def test_no_pages_scanned_and_scanner_not_found_are_exceptions():
    assert issubclass(NoPagesScannedError, Exception)
    assert issubclass(ScannerNotFoundError, Exception)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pip install -e ".[dev]" && pytest tests/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scanix500.errors'`

- [ ] **Step 4: Implement errors.py**

```python
# src/scanix500/errors.py
class ScannerNotFoundError(Exception):
    """Raised when the SANE fujitsu backend can't find the iX500."""


class NoPagesScannedError(Exception):
    """Raised when the ADF was empty and no pages were captured."""


class MultiFeedError(Exception):
    """Raised when the scanner's multi-feed sensor triggers mid-batch.

    Carries the pages already captured successfully so the caller can
    still process them instead of discarding the whole batch.
    """

    def __init__(self, sheet_index: int, pages_captured: list):
        super().__init__(f"Multi-feed detected at sheet {sheet_index}")
        self.sheet_index = sheet_index
        self.pages_captured = pages_captured
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_errors.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/scanix500/__init__.py src/scanix500/errors.py tests/__init__.py tests/test_errors.py
git commit -m "Scaffold scanix500 package with error types"
```

---

### Task 2: Capture orchestration logic

**Files:**
- Create: `src/scanix500/capture.py`
- Test: `tests/test_capture.py`

**Interfaces:**
- Consumes: `scanix500.errors.MultiFeedError`, `scanix500.errors.NoPagesScannedError` (Task 1).
- Produces: `scanix500.capture.PagePair(front: PIL.Image.Image, back: PIL.Image.Image)`, `scanix500.capture.SaneDevice` (Protocol with `start()`, `read_frame()`, `multi_feed_detected()`), `scanix500.capture.capture_pages(device: SaneDevice) -> list[PagePair]` — used by Task 7 (cli.py) and Task 3 (real adapter).

- [ ] **Step 1: Write the failing test for the happy-path capture loop**

```python
# tests/test_capture.py
import pytest
from PIL import Image

from scanix500.capture import PagePair, capture_pages
from scanix500.errors import MultiFeedError, NoPagesScannedError


def _img(tag):
    im = Image.new("RGB", (2, 2))
    im.info["tag"] = tag
    return im


class FakeSaneDevice:
    def __init__(self, frames, multi_feed_after_sheet=None):
        self._frames = iter(frames)
        self._multi_feed_after_sheet = multi_feed_after_sheet
        self._sheets_seen = 0
        self.started = False

    def start(self):
        self.started = True

    def read_frame(self):
        return next(self._frames)

    def multi_feed_detected(self):
        self._sheets_seen += 1
        return self._sheets_seen == self._multi_feed_after_sheet


def test_capture_pages_pairs_front_and_back_in_order():
    frames = [_img("f1"), _img("b1"), _img("f2"), _img("b2")]
    device = FakeSaneDevice(frames)

    pages = capture_pages(device)

    assert device.started
    assert len(pages) == 2
    assert pages[0] == PagePair(frames[0], frames[1])
    assert pages[1] == PagePair(frames[2], frames[3])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_capture.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scanix500.capture'`

- [ ] **Step 3: Implement PagePair, SaneDevice protocol, and the happy-path loop**

```python
# src/scanix500/capture.py
from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from scanix500.errors import MultiFeedError, NoPagesScannedError


@dataclass
class PagePair:
    front: Image.Image
    back: Image.Image


class SaneDevice(Protocol):
    def start(self) -> None: ...
    def read_frame(self) -> Image.Image: ...
    def multi_feed_detected(self) -> bool: ...


def capture_pages(device: SaneDevice) -> list[PagePair]:
    device.start()
    pages: list[PagePair] = []
    while True:
        try:
            front = device.read_frame()
        except StopIteration:
            break
        back = device.read_frame()
        pages.append(PagePair(front, back))

    if not pages:
        raise NoPagesScannedError()
    return pages
```

`PagePair` needs equality by identity of its images for the test above to pass as written (dataclass default `__eq__` compares field-by-field with `==`; `Image.Image` doesn't define `__eq__` beyond identity, so this works since the test reuses the same objects).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_capture.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/capture.py tests/test_capture.py
git commit -m "Add capture_pages happy-path duplex pairing loop"
```

- [ ] **Step 6: Write the failing test for multi-feed detection**

```python
# add to tests/test_capture.py
def test_capture_pages_raises_multi_feed_error_with_partial_pages():
    frames = [_img("f1"), _img("b1"), _img("f2"), _img("b2"), _img("f3"), _img("b3")]
    device = FakeSaneDevice(frames, multi_feed_after_sheet=2)

    with pytest.raises(MultiFeedError) as exc_info:
        capture_pages(device)

    err = exc_info.value
    assert err.sheet_index == 2
    assert len(err.pages_captured) == 2
```

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest tests/test_capture.py -v`
Expected: FAIL — no `MultiFeedError` is ever raised, so `pytest.raises` fails.

- [ ] **Step 8: Add multi-feed detection to the loop**

```python
# in capture_pages, replace the loop body with:
def capture_pages(device: SaneDevice) -> list[PagePair]:
    device.start()
    pages: list[PagePair] = []
    sheet_index = 0
    while True:
        try:
            front = device.read_frame()
        except StopIteration:
            break
        back = device.read_frame()
        sheet_index += 1
        pages.append(PagePair(front, back))
        if device.multi_feed_detected():
            raise MultiFeedError(sheet_index, pages)

    if not pages:
        raise NoPagesScannedError()
    return pages
```

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest tests/test_capture.py -v`
Expected: PASS (2 tests)

- [ ] **Step 10: Commit**

```bash
git add src/scanix500/capture.py tests/test_capture.py
git commit -m "Raise MultiFeedError with partial pages on multi-feed sensor trip"
```

- [ ] **Step 11: Write the failing test for an empty ADF**

```python
# add to tests/test_capture.py
def test_capture_pages_raises_when_adf_is_empty():
    device = FakeSaneDevice(frames=[])

    with pytest.raises(NoPagesScannedError):
        capture_pages(device)
```

- [ ] **Step 12: Run test to verify it fails**

Run: `pytest tests/test_capture.py -v`

If Step 8's implementation is already correct, this may already PASS — that's expected here since the empty-ADF branch was written proactively in Step 8. Confirm by running; if it already passes, skip to Step 14 (no code change needed) rather than editing anything.

- [ ] **Step 13: (Only if Step 12 failed) fix the empty-ADF branch**

Not expected to be needed given Step 8's implementation, but if it failed, ensure the `if not pages: raise NoPagesScannedError()` line is present exactly as in Step 8.

- [ ] **Step 14: Run full test file to confirm all three tests pass**

Run: `pytest tests/test_capture.py -v`
Expected: PASS (3 tests)

- [ ] **Step 15: Commit**

```bash
git add tests/test_capture.py
git commit -m "Add empty-ADF test coverage for capture_pages"
```

---

### Task 3: Real SANE device adapter (hardware-only, manually verified)

**Files:**
- Modify: `src/scanix500/capture.py` (append adapter class)

**Interfaces:**
- Consumes: `scanix500.capture.SaneDevice` protocol (Task 2), `scanix500.errors.ScannerNotFoundError` (Task 1).
- Produces: `scanix500.capture.PySaneDevice()` — a concrete `SaneDevice` implementation used only by `cli.py` (Task 7). Not covered by automated tests; verified via Task 8's manual checklist.

- [ ] **Step 1: Implement the adapter, importing `sane` lazily**

```python
# append to src/scanix500/capture.py
from scanix500.errors import ScannerNotFoundError


class PySaneDevice:
    """Wraps python-sane to satisfy the SaneDevice protocol.

    Imports `sane` lazily so unit tests (and any environment without
    libsane installed) never need it — only real hardware runs touch
    this class.
    """

    def __init__(self):
        import sane

        self._sane = sane
        sane.init()
        devices = sane.get_devices()
        fujitsu_devices = [d for d in devices if "fujitsu" in d[1].lower() or "ix500" in d[1].lower()]
        if not fujitsu_devices:
            raise ScannerNotFoundError("No iX500 found via SANE fujitsu backend")
        self._dev = sane.open(fujitsu_devices[0][0])
        self._dev.source = "ADF Duplex"

    def start(self) -> None:
        self._dev.start()

    def read_frame(self):
        return self._dev.snap()

    def multi_feed_detected(self) -> bool:
        try:
            return bool(self._dev.double_feed_detected)
        except AttributeError:
            return False
```

Note in the plan: the exact option name for multi-feed status (`double_feed_detected` above) and the exact device-matching string are best guesses pending Task 8's hardware verification — adjust both against `scanimage --help -d <device>`'s reported option names and `sane.get_devices()` output on the real machine before relying on this class.

- [ ] **Step 2: Commit**

```bash
git add src/scanix500/capture.py
git commit -m "Add PySaneDevice adapter (hardware-verified in Task 8)"
```

---

### Task 4: Blank-page filtering and separator splitting

**Files:**
- Create: `src/scanix500/blank_filter.py`
- Test: `tests/test_blank_filter.py`

**Interfaces:**
- Consumes: `scanix500.capture.PagePair` (Task 2).
- Produces: `scanix500.blank_filter.is_blank(image: PIL.Image.Image, threshold: float = 0.02) -> bool`, `scanix500.blank_filter.filter_pages(pages: list[PagePair], *, threshold: float = 0.02, split_on_blank: bool = False) -> list[list[PIL.Image.Image]]` — used by Task 7 (cli.py).

- [ ] **Step 1: Write the failing test for is_blank**

```python
# tests/test_blank_filter.py
from PIL import Image, ImageDraw

from scanix500.blank_filter import filter_pages, is_blank
from scanix500.capture import PagePair


def _blank_image():
    return Image.new("L", (100, 100), color=255)


def _content_image():
    im = Image.new("L", (100, 100), color=255)
    draw = ImageDraw.Draw(im)
    draw.rectangle([10, 10, 90, 90], fill=0)
    return im


def test_is_blank_true_for_all_white_image():
    assert is_blank(_blank_image()) is True


def test_is_blank_false_for_image_with_content():
    assert is_blank(_content_image()) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_blank_filter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scanix500.blank_filter'`

- [ ] **Step 3: Implement is_blank**

```python
# src/scanix500/blank_filter.py
from PIL import Image


def is_blank(image: Image.Image, threshold: float = 0.02) -> bool:
    gray = image.convert("L")
    histogram = gray.histogram()
    total_pixels = gray.width * gray.height
    non_background = sum(
        count for value, count in enumerate(histogram) if value < 250
    )
    return (non_background / total_pixels) < threshold
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_blank_filter.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/blank_filter.py tests/test_blank_filter.py
git commit -m "Add is_blank ink-coverage detection"
```

- [ ] **Step 6: Write the failing test for filter_pages dropping a blank backside**

```python
# add to tests/test_blank_filter.py
def test_filter_pages_drops_blank_backside_keeps_front():
    pages = [PagePair(_content_image(), _blank_image())]

    partitions = filter_pages(pages)

    assert partitions == [[pages[0].front]]


def test_filter_pages_keeps_both_sides_when_neither_blank():
    pages = [PagePair(_content_image(), _content_image())]

    partitions = filter_pages(pages)

    assert partitions == [[pages[0].front, pages[0].back]]
```

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest tests/test_blank_filter.py -v`
Expected: FAIL with `ImportError: cannot import name 'filter_pages'`

- [ ] **Step 8: Implement filter_pages (without split-on-blank yet)**

```python
# add to src/scanix500/blank_filter.py
from scanix500.capture import PagePair


def filter_pages(
    pages: list[PagePair],
    *,
    threshold: float = 0.02,
    split_on_blank: bool = False,
) -> list[list[Image.Image]]:
    partitions: list[list[Image.Image]] = [[]]
    for pair in pages:
        front_blank = is_blank(pair.front, threshold)
        back_blank = is_blank(pair.back, threshold)

        if front_blank and back_blank:
            if split_on_blank and partitions[-1]:
                partitions.append([])
            continue

        if not front_blank:
            partitions[-1].append(pair.front)
        if not back_blank:
            partitions[-1].append(pair.back)

    return [p for p in partitions if p]
```

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest tests/test_blank_filter.py -v`
Expected: PASS (4 tests)

- [ ] **Step 10: Commit**

```bash
git add src/scanix500/blank_filter.py tests/test_blank_filter.py
git commit -m "Add filter_pages dropping blank sides"
```

- [ ] **Step 11: Write the failing test for split_on_blank**

```python
# add to tests/test_blank_filter.py
def test_filter_pages_splits_on_fully_blank_separator_sheet():
    doc1_page = PagePair(_content_image(), _content_image())
    separator = PagePair(_blank_image(), _blank_image())
    doc2_page = PagePair(_content_image(), _content_image())
    pages = [doc1_page, separator, doc2_page]

    partitions = filter_pages(pages, split_on_blank=True)

    assert len(partitions) == 2
    assert partitions[0] == [doc1_page.front, doc1_page.back]
    assert partitions[1] == [doc2_page.front, doc2_page.back]


def test_filter_pages_without_split_on_blank_ignores_separator_boundary():
    doc1_page = PagePair(_content_image(), _content_image())
    separator = PagePair(_blank_image(), _blank_image())
    doc2_page = PagePair(_content_image(), _content_image())
    pages = [doc1_page, separator, doc2_page]

    partitions = filter_pages(pages, split_on_blank=False)

    assert len(partitions) == 1
    assert partitions[0] == [
        doc1_page.front,
        doc1_page.back,
        doc2_page.front,
        doc2_page.back,
    ]
```

- [ ] **Step 12: Run test to verify it fails or passes**

Run: `pytest tests/test_blank_filter.py -v`

Step 8's implementation already handles both branches, so this is expected to PASS immediately — confirming the earlier implementation was correct rather than requiring new code.

- [ ] **Step 13: Run full test file to confirm all six tests pass**

Run: `pytest tests/test_blank_filter.py -v`
Expected: PASS (6 tests)

- [ ] **Step 14: Commit**

```bash
git add tests/test_blank_filter.py
git commit -m "Add split_on_blank separator-sheet test coverage"
```

---

### Task 5: PDF assembly with per-page OCR fallback

**Files:**
- Create: `src/scanix500/pdf_builder.py`
- Test: `tests/test_pdf_builder.py`

**Interfaces:**
- Produces: `scanix500.pdf_builder.build_pdf(images: list[PIL.Image.Image], output_path: pathlib.Path, *, ocr: bool = True) -> None` — used by Task 7 (cli.py).

- [ ] **Step 1: Write the failing test for image-only assembly (ocr=False)**

```python
# tests/test_pdf_builder.py
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from scanix500.pdf_builder import build_pdf


def _content_image():
    return Image.new("RGB", (100, 100), color=(255, 0, 0))


def test_build_pdf_without_ocr_produces_correct_page_count(tmp_path):
    images = [_content_image(), _content_image()]
    output_path = tmp_path / "out.pdf"

    build_pdf(images, output_path, ocr=False)

    reader = PdfReader(str(output_path))
    assert len(reader.pages) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pdf_builder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scanix500.pdf_builder'`

- [ ] **Step 3: Implement build_pdf for the ocr=False path**

```python
# src/scanix500/pdf_builder.py
import logging
import subprocess
import tempfile
from pathlib import Path

from PIL import Image
from pypdf import PdfWriter

logger = logging.getLogger(__name__)


def _save_image_only_pdf(image: Image.Image, path: Path) -> None:
    image.convert("RGB").save(path, format="PDF")


def _merge_pdfs(page_paths: list[Path], output_path: Path) -> None:
    writer = PdfWriter()
    for page_path in page_paths:
        writer.append(str(page_path))
    with open(output_path, "wb") as f:
        writer.write(f)


def build_pdf(images: list[Image.Image], output_path: Path, *, ocr: bool = True) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        page_paths = []
        for i, image in enumerate(images):
            page_path = tmp_dir_path / f"page_{i}.pdf"
            _save_image_only_pdf(image, page_path)
            page_paths.append(page_path)
        _merge_pdfs(page_paths, output_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pdf_builder.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/pdf_builder.py tests/test_pdf_builder.py
git commit -m "Add build_pdf image-only assembly path"
```

- [ ] **Step 6: Write the failing test for OCR success calling ocrmypdf per page**

```python
# add to tests/test_pdf_builder.py
from unittest.mock import patch


def test_build_pdf_with_ocr_invokes_ocrmypdf_per_page(tmp_path):
    images = [_content_image(), _content_image()]
    output_path = tmp_path / "out.pdf"

    def fake_run(cmd, check, capture_output):
        # cmd = ["ocrmypdf", str(input_path), str(output_path)]
        input_path, ocred_path = Path(cmd[1]), Path(cmd[2])
        ocred_path.write_bytes(input_path.read_bytes())
        return None

    with patch("scanix500.pdf_builder.subprocess.run", side_effect=fake_run) as mock_run:
        build_pdf(images, output_path, ocr=True)

    assert mock_run.call_count == 2
    reader = PdfReader(str(output_path))
    assert len(reader.pages) == 2
```

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest tests/test_pdf_builder.py -v`
Expected: FAIL — `build_pdf` never calls `subprocess.run`, so `mock_run.call_count == 2` fails (`0 != 2`).

- [ ] **Step 8: Implement the OCR path with per-page fallback**

```python
# replace build_pdf in src/scanix500/pdf_builder.py
def _ocr_single_page(input_path: Path) -> Path:
    output_path = input_path.with_suffix(".ocr.pdf")
    try:
        subprocess.run(
            ["ocrmypdf", str(input_path), str(output_path)],
            check=True,
            capture_output=True,
        )
        return output_path
    except subprocess.CalledProcessError:
        logger.warning("OCR failed for %s; including as image-only", input_path)
        return input_path


def build_pdf(images: list[Image.Image], output_path: Path, *, ocr: bool = True) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        page_paths = []
        for i, image in enumerate(images):
            page_path = tmp_dir_path / f"page_{i}.pdf"
            _save_image_only_pdf(image, page_path)
            page_paths.append(_ocr_single_page(page_path) if ocr else page_path)
        _merge_pdfs(page_paths, output_path)
```

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest tests/test_pdf_builder.py -v`
Expected: PASS (2 tests)

- [ ] **Step 10: Commit**

```bash
git add src/scanix500/pdf_builder.py tests/test_pdf_builder.py
git commit -m "Add per-page ocrmypdf invocation"
```

- [ ] **Step 11: Write the failing test for OCR failure fallback**

```python
# add to tests/test_pdf_builder.py
import subprocess as subprocess_module


def test_build_pdf_falls_back_to_image_only_when_ocr_fails(tmp_path):
    images = [_content_image()]
    output_path = tmp_path / "out.pdf"

    def failing_run(cmd, check, capture_output):
        raise subprocess_module.CalledProcessError(returncode=1, cmd=cmd)

    with patch("scanix500.pdf_builder.subprocess.run", side_effect=failing_run):
        build_pdf(images, output_path, ocr=True)  # must not raise

    reader = PdfReader(str(output_path))
    assert len(reader.pages) == 1
```

- [ ] **Step 12: Run test to verify it fails or passes**

Run: `pytest tests/test_pdf_builder.py -v`

Step 8's `_ocr_single_page` already catches `CalledProcessError` and falls back, so this is expected to PASS immediately, confirming that behavior.

- [ ] **Step 13: Run full test file to confirm all three tests pass**

Run: `pytest tests/test_pdf_builder.py -v`
Expected: PASS (3 tests)

- [ ] **Step 14: Commit**

```bash
git add tests/test_pdf_builder.py
git commit -m "Add OCR-failure fallback test coverage"
```

---

### Task 6: Output path routing

**Files:**
- Create: `src/scanix500/router.py`
- Test: `tests/test_router.py`

**Interfaces:**
- Produces: `scanix500.router.compute_output_paths(destination: pathlib.Path, count: int, *, now: datetime.datetime | None = None) -> list[pathlib.Path]` — used by Task 7 (cli.py).

- [ ] **Step 1: Write the failing test for a single output path**

```python
# tests/test_router.py
from datetime import datetime
from pathlib import Path

from scanix500.router import compute_output_paths


def test_compute_output_paths_single_file_has_timestamp_name():
    fixed_now = datetime(2026, 9, 13, 14, 30, 12)

    paths = compute_output_paths(Path("/dest"), count=1, now=fixed_now)

    assert paths == [Path("/dest/scan_2026-09-13_143012.pdf")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scanix500.router'`

- [ ] **Step 3: Implement compute_output_paths for count == 1**

```python
# src/scanix500/router.py
from datetime import datetime
from pathlib import Path


def compute_output_paths(
    destination: Path, count: int, *, now: datetime | None = None
) -> list[Path]:
    timestamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    if count == 1:
        return [destination / f"scan_{timestamp}.pdf"]
    return [destination / f"scan_{timestamp}_{i + 1}.pdf" for i in range(count)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_router.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/router.py tests/test_router.py
git commit -m "Add compute_output_paths for single-file case"
```

- [ ] **Step 6: Write the failing test for multiple output paths**

```python
# add to tests/test_router.py
def test_compute_output_paths_multiple_files_are_sequentially_numbered():
    fixed_now = datetime(2026, 9, 13, 14, 30, 12)

    paths = compute_output_paths(Path("/dest"), count=3, now=fixed_now)

    assert paths == [
        Path("/dest/scan_2026-09-13_143012_1.pdf"),
        Path("/dest/scan_2026-09-13_143012_2.pdf"),
        Path("/dest/scan_2026-09-13_143012_3.pdf"),
    ]
```

- [ ] **Step 7: Run test to verify it fails or passes**

Run: `pytest tests/test_router.py -v`

Step 3's implementation already handles `count > 1`, so this is expected to PASS immediately, confirming that branch.

- [ ] **Step 8: Run full test file to confirm both tests pass**

Run: `pytest tests/test_router.py -v`
Expected: PASS (2 tests)

- [ ] **Step 9: Commit**

```bash
git add tests/test_router.py
git commit -m "Add multi-file sequential numbering test coverage"
```

---

### Task 7: CLI wiring

**Files:**
- Create: `src/scanix500/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `scanix500.capture.capture_pages`, `scanix500.capture.PySaneDevice`, `scanix500.blank_filter.filter_pages`, `scanix500.pdf_builder.build_pdf`, `scanix500.router.compute_output_paths`, `scanix500.errors.{ScannerNotFoundError, MultiFeedError, NoPagesScannedError}` (Tasks 1-6).
- Produces: `scanix500.cli.main(argv: list[str] | None = None) -> int` — the `scanix500` console-script entry point (registered in Task 1's `pyproject.toml`).

- [ ] **Step 1: Write the failing test for the default happy path**

```python
# tests/test_cli.py
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from scanix500.capture import PagePair
from scanix500.cli import main


def _content_image():
    im = Image.new("L", (100, 100), color=255)
    im.paste(0, (10, 10, 90, 90))
    return im


def test_main_default_flags_produce_one_pdf(tmp_path, capsys):
    pages = [PagePair(_content_image(), _content_image())]

    with patch("scanix500.cli.PySaneDevice"), patch(
        "scanix500.cli.capture_pages", return_value=pages
    ), patch("scanix500.pdf_builder.subprocess.run") as mock_ocr_run:
        mock_ocr_run.side_effect = lambda cmd, check, capture_output: Path(
            cmd[2]
        ).write_bytes(Path(cmd[1]).read_bytes())

        exit_code = main([str(tmp_path)])

    assert exit_code == 0
    output_files = list(tmp_path.glob("scan_*.pdf"))
    assert len(output_files) == 1
    printed = capsys.readouterr().out
    assert str(output_files[0]) in printed
```

Patch target is `scanix500.pdf_builder.subprocess.run` (where the OCR subprocess call actually happens), not `scanix500.cli.subprocess.run` — `cli.py` never calls `subprocess` directly.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scanix500.cli'`

- [ ] **Step 3: Implement cli.py**

```python
# src/scanix500/cli.py
import argparse
import sys
from pathlib import Path

from scanix500.blank_filter import filter_pages
from scanix500.capture import PySaneDevice, capture_pages
from scanix500.errors import MultiFeedError, NoPagesScannedError, ScannerNotFoundError
from scanix500.pdf_builder import build_pdf
from scanix500.router import compute_output_paths


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="scanix500")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--skip-blank-filter", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--split-on-blank", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        device = PySaneDevice()
        pages = capture_pages(device)
    except ScannerNotFoundError as e:
        print(f"Scanner not found: {e}", file=sys.stderr)
        return 1
    except NoPagesScannedError:
        print("No pages scanned.")
        return 0
    except MultiFeedError as e:
        print(
            f"Multi-feed detected at sheet {e.sheet_index}; "
            f"processing {len(e.pages_captured)} pages captured before the jam.",
            file=sys.stderr,
        )
        pages = e.pages_captured
        exit_code = 1
    else:
        exit_code = 0

    if args.skip_blank_filter:
        partitions = [
            [img for pair in pages for img in (pair.front, pair.back)]
        ]
    else:
        partitions = filter_pages(pages, split_on_blank=args.split_on_blank)

    output_paths = compute_output_paths(args.destination, len(partitions))
    for images, path in zip(partitions, output_paths):
        build_pdf(images, path, ocr=not args.skip_ocr)
        print(path)

    return exit_code
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/cli.py tests/test_cli.py
git commit -m "Wire capture, filter, OCR, and routing into scanix500 CLI"
```

- [ ] **Step 6: Write the failing test for --split-on-blank producing multiple files**

```python
# add to tests/test_cli.py
def _blank_image():
    return Image.new("L", (100, 100), color=255)


def test_main_split_on_blank_produces_multiple_pdfs(tmp_path):
    doc1 = PagePair(_content_image(), _content_image())
    separator = PagePair(_blank_image(), _blank_image())
    doc2 = PagePair(_content_image(), _content_image())
    pages = [doc1, separator, doc2]

    with patch("scanix500.cli.PySaneDevice"), patch(
        "scanix500.cli.capture_pages", return_value=pages
    ), patch("scanix500.pdf_builder.subprocess.run") as mock_ocr_run:
        mock_ocr_run.side_effect = lambda cmd, check, capture_output: Path(
            cmd[2]
        ).write_bytes(Path(cmd[1]).read_bytes())

        exit_code = main([str(tmp_path), "--split-on-blank"])

    assert exit_code == 0
    output_files = sorted(tmp_path.glob("scan_*.pdf"))
    assert len(output_files) == 2
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`

The Step 3 implementation already threads `args.split_on_blank` into `filter_pages`, so this is expected to PASS immediately, confirming that wiring.

- [ ] **Step 8: Run full test file to confirm both tests pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (2 tests)

- [ ] **Step 9: Commit**

```bash
git add tests/test_cli.py
git commit -m "Add --split-on-blank CLI integration test coverage"
```

---

### Task 8: Installation guide and manual hardware test checklist

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write the installation and manual test checklist**

```markdown
# scanix500

A vendor-independent scan pipeline for the ScanSnap iX500, driving it
directly via SANE instead of ScanSnap Home.

## Install

    brew install sane-backends ocrmypdf tesseract
    pip install python-sane
    pip install -e ".[dev]"

## Run

    scanix500 ~/Documents/Scans
    scanix500 ~/Documents/Scans --split-on-blank
    scanix500 ~/Documents/Scans --skip-ocr
    scanix500 ~/Documents/Scans --skip-blank-filter

## Automated tests

    pytest

These cover all pure logic (capture pairing/multi-feed/empty-ADF handling,
blank detection and splitting, PDF assembly and OCR fallback, output path
naming, and CLI wiring) using fakes and mocks — no scanner hardware
required.

## Manual hardware checklist

Run these by hand against the real iX500 after any change to
`capture.py` or `pdf_builder.py` — hardware behavior isn't mocked:

- [ ] `scanimage -L` and/or `sane.get_devices()` shows the iX500 via the
      `fujitsu` backend. If not, `PySaneDevice`'s device-matching string
      needs adjusting (see Task 3's note).
- [ ] Load a 5-page double-sided stack, run `scanix500 <dest>`: resulting
      PDF has 10 pages in correct front/back/front/back order.
- [ ] Load a stack with one intentionally blank backside: resulting PDF
      has that blank page removed.
- [ ] Load a stack with a fully blank separator sheet in the middle, run
      with `--split-on-blank`: two separate PDFs are produced.
- [ ] Deliberately feed two sheets stuck together: `scanix500` reports a
      multi-feed error and exits non-zero, and the pages captured before
      the jam are still written to a PDF. Confirm `PySaneDevice`'s
      `multi_feed_detected()` option name actually reflects the sensor
      (adjust per Task 3's note if the option name is wrong for this
      firmware/backend version).
- [ ] Resulting PDF text is selectable/searchable (OCR ran) unless
      `--skip-ocr` was passed.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Add install instructions and manual hardware test checklist"
```
