# Canon CanoScan 8800F (flatbed documents) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Canon CanoScan 8800F plugged into the same machine as the iX500 shows up in `GET /scanners` and scans single flatbed pages to (searchable) PDF, on `scanix500-server` (Pi) and in the Mac menu bar app, including the 8800F's button on the Mac.

**Architecture:** A new `scanners.py` holds a catalogue of known scanners and discovers which are plugged in from the USB bus (sysfs / `ioreg`), never opening SANE. The chosen scanner id travels `POST /scan` -> `Profile.scanner` -> CLI `--scanner`; the CLI captures with a new one-frame `PySaneFlatbed` and skips trim/snap/blank-filter for it. The spec is `docs/superpowers/specs/2026-10-24-canoscan-8800f-flatbed-design.md`.

**Tech Stack:** Python 3.11+, python-sane (SANE `pixma` backend), pytest, rumps (Mac app only).

## Global Constraints

- No protocol change: `PROTOCOL.md` (dossiary-scan-helper) stays as it is; `format` stays `"pdf"`, `sources` stays `flatbed`/`feeder`.
- USB discovery never opens SANE (it would race a running scan or the button poller) and never raises into the HTTP thread: on any failure it returns "nothing attached".
- The iX500 pipeline is not touched: same capture, trim, snap, blank filter, defaults. `profiles.json` files written before this change load unchanged.
- Hardware-touching code (`PySaneFlatbed`'s SANE calls, the pixma button) is verified manually on the real 8800F, not mocked; unit-test only orchestration (this repo's rule, see AGENTS.md "Key constraints").
- Error text sent to Dossiary stays English (like `NOT_CONNECTED_MESSAGE` today); no new `t()` strings.
- `scanix500.server` must not import rumps/AppKit (`tests/server/test_runtime.py` checks it).
- Run tests with `.venv/bin/python -m pytest` from the repo root. Baseline before this plan: **353 passed**.
- Work on a branch off `spec/canoscan-8800f` (or merge the spec branch first); never commit to `main`. End commit messages with the attribution lines from the session's system reminder.

---

### Task 1: Scanner catalogue and USB discovery

**Files:**
- Create: `src/scanix500/scanners.py`
- Create: `tests/test_scanners.py`
- Modify: `tests/conftest.py` (autouse "iX500 is attached" stub)

**Interfaces:**
- Produces:
  - `IX500_ID = "sane:ix500"`, `CANOSCAN_8800F_ID = "sane:canoscan-8800f"`
  - `IX500: dict`, `CANOSCAN_8800F: dict` (the `GET /scanners` entries)
  - `@dataclass(frozen=True) KnownScanner(id: str, name: str, usb_id: tuple[int, int], entry: dict, not_found_text: str, not_connected_message: str)`
  - `KNOWN_SCANNERS: tuple[KnownScanner, ...]` (iX500 first, then the 8800F)
  - `get_scanner(scanner_id: object) -> KnownScanner | None`
  - `usb_ids_from_sysfs(root: Path = SYSFS_USB) -> set[tuple[int, int]]`
  - `usb_ids_from_ioreg(text: str) -> set[tuple[int, int]]`
  - `usb_ids() -> set[tuple[int, int]]` (platform dispatch, never raises)
  - `attached_scanners(ids: set[tuple[int, int]] | None = None) -> list[dict]` (fresh dict copies, catalogue order)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scanners.py`:

```python
from pathlib import Path

from scanix500 import scanners
from scanix500.scanners import (
    CANOSCAN_8800F, CANOSCAN_8800F_ID, IX500, IX500_ID, KNOWN_SCANNERS, attached_scanners, get_scanner,
    usb_ids, usb_ids_from_ioreg, usb_ids_from_sysfs,
)

# Shape of `ioreg -p IOUSB -l -w0` (checked on macOS 27): device nodes start with "+-o",
# their properties follow, decimal ids, idProduct before idVendor.
IOREG_SAMPLE = """\
+-o Root  <class IORegistryEntry, id 0x100000100, retain 37>
  +-o AppleT8132USBXHCI@03000000  <class AppleT8132USBXHCI, id 0x1000004c2, registered, matched, active>
  | +-o USB2.0 Hub@03100000  <class IOUSBHostDevice, id 0x100000b7e, registered, matched, active>
  | | {
  | |   "idProduct" = 22529
  | |   "idVendor" = 7516
  | | }
  | | +-o ScanSnap iX500@03130000  <class IOUSBHostDevice, id 0x100000dd2, registered, matched, active>
  | | | {
  | | |   "idProduct" = 4907
  | | |   "idVendor" = 1221
  | | | }
  | | +-o CanoScan@03140000  <class IOUSBHostDevice, id 0x100000ddf, registered, matched, active>
  | | | {
  | | |   "idProduct" = 6401
  | | |   "idVendor" = 1193
  | | | }
"""


def test_ioreg_parser_pairs_each_devices_vendor_and_product():
    assert usb_ids_from_ioreg(IOREG_SAMPLE) == {(7516, 22529), (0x04C5, 0x132B), (0x04A9, 0x1901)}


def test_ioreg_parser_ignores_garbage():
    assert usb_ids_from_ioreg("") == set()
    assert usb_ids_from_ioreg('"idVendor" = 1\nnothing else') == set()


def _device(root: Path, name: str, vendor: str, product: str) -> None:
    (root / name).mkdir()
    (root / name / "idVendor").write_text(vendor + "\n")
    (root / name / "idProduct").write_text(product + "\n")


def test_sysfs_reader_reads_hex_ids_and_skips_entries_without_them(tmp_path):
    _device(tmp_path, "1-1", "04c5", "132b")
    _device(tmp_path, "1-1.4", "04a9", "1901")
    (tmp_path / "usb1").mkdir()  # a root hub entry with no id files
    _device(tmp_path, "1-9", "zzzz", "0001")  # unreadable id
    assert usb_ids_from_sysfs(tmp_path) == {(0x04C5, 0x132B), (0x04A9, 0x1901)}


def test_sysfs_reader_returns_nothing_when_the_folder_is_missing(tmp_path):
    assert usb_ids_from_sysfs(tmp_path / "nope") == set()


def test_usb_ids_never_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("no ioreg")

    monkeypatch.setattr(scanners.sys, "platform", "darwin")
    monkeypatch.setattr(scanners.subprocess, "run", boom)
    assert usb_ids() == set()


def test_usb_ids_runs_ioreg_on_macos(monkeypatch):
    class Done:
        stdout = IOREG_SAMPLE

    seen = {}
    monkeypatch.setattr(scanners.sys, "platform", "darwin")
    monkeypatch.setattr(scanners.subprocess, "run", lambda cmd, **k: seen.update(cmd=cmd) or Done())
    assert (0x04A9, 0x1901) in usb_ids()
    assert seen["cmd"][:3] == ["ioreg", "-p", "IOUSB"]


def test_usb_ids_reads_sysfs_elsewhere(monkeypatch, tmp_path):
    _device(tmp_path, "1-1", "04a9", "1901")
    monkeypatch.setattr(scanners.sys, "platform", "linux")
    monkeypatch.setattr(scanners, "SYSFS_USB", tmp_path)
    assert usb_ids() == {(0x04A9, 0x1901)}


def test_attached_scanners_lists_only_what_is_plugged_in_in_catalogue_order():
    both = {(0x04C5, 0x132B), (0x04A9, 0x1901), (1, 2)}
    assert [s["id"] for s in attached_scanners(both)] == [IX500_ID, CANOSCAN_8800F_ID]
    assert [s["id"] for s in attached_scanners({(0x04A9, 0x1901)})] == [CANOSCAN_8800F_ID]
    assert attached_scanners(set()) == []


def test_attached_scanners_returns_copies_that_can_be_changed_freely():
    entry = attached_scanners({(0x04C5, 0x132B)})[0]
    entry["extras"].append("changed")
    entry["ocr"]["languages"].clear()
    assert IX500["extras"] == ["splitOnBlank", "skipBlankPages"]
    assert IX500["ocr"]["languages"][0] == "deu"


def test_attached_scanners_defaults_to_the_real_usb_bus(monkeypatch):
    monkeypatch.setattr(scanners, "usb_ids", lambda: {(0x04A9, 0x1901)})
    assert [s["id"] for s in attached_scanners()] == [CANOSCAN_8800F_ID]


def test_get_scanner():
    assert get_scanner(IX500_ID).name == "ScanSnap iX500"
    assert get_scanner(CANOSCAN_8800F_ID).usb_id == (0x04A9, 0x1901)
    assert get_scanner("fake:flatbed") is None
    assert get_scanner(None) is None and get_scanner(3) is None


def test_the_8800f_entry_is_a_flatbed_without_extras():
    assert CANOSCAN_8800F["sources"] == ["flatbed"] and CANOSCAN_8800F["duplex"] is False
    assert CANOSCAN_8800F["extras"] == []
    assert CANOSCAN_8800F["resolutions"] == [150, 300, 600]
    assert CANOSCAN_8800F["colorModes"] == ["color", "gray", "bw"]
    assert CANOSCAN_8800F["name"] == "Canon CanoScan 8800F"


def test_catalogue_ids_and_usb_ids_are_unique_and_match_their_entries():
    assert len({s.id for s in KNOWN_SCANNERS}) == len(KNOWN_SCANNERS)
    assert len({s.usb_id for s in KNOWN_SCANNERS}) == len(KNOWN_SCANNERS)
    for s in KNOWN_SCANNERS:
        assert s.entry["id"] == s.id and s.entry["name"] == s.name
        assert s.name in s.not_connected_message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_scanners.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'scanix500.scanners'`.

- [ ] **Step 3: Write the implementation**

Create `src/scanix500/scanners.py`:

```python
"""The scanners scanix500 knows, and which of them are plugged in.

Discovery reads the USB bus (sysfs on Linux, ioreg on macOS) and never opens
SANE: opening a device from the HTTP thread would race a running scan or the
Scan-button poller."""

from __future__ import annotations

import copy
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SYSFS_USB = Path("/sys/bus/usb/devices")

IX500_ID = "sane:ix500"
CANOSCAN_8800F_ID = "sane:canoscan-8800f"

# The GET /scanners entries (PROTOCOL.md "GET /scanners").
IX500 = {
    "id": IX500_ID,
    "name": "ScanSnap iX500",
    "sources": ["feeder"],
    "duplex": True,
    "colorModes": ["color", "gray", "bw"],
    "resolutions": [300, 600],
    "extras": ["splitOnBlank", "skipBlankPages"],
    "ocr": {"languages": ["deu", "eng", "fra", "spa", "chi_sim", "chi_tra"]},
}

# One A4 page per scan from the glass; no feeder, so no blank-page extras.
CANOSCAN_8800F = {
    "id": CANOSCAN_8800F_ID,
    "name": "Canon CanoScan 8800F",
    "sources": ["flatbed"],
    "duplex": False,
    "colorModes": ["color", "gray", "bw"],
    "resolutions": [150, 300, 600],
    "extras": [],
    "ocr": {"languages": ["deu", "eng", "fra", "spa", "chi_sim", "chi_tra"]},
}


@dataclass(frozen=True)
class KnownScanner:
    id: str
    name: str
    usb_id: tuple[int, int]  # (vendor, product)
    entry: dict  # the GET /scanners entry
    not_found_text: str  # what the CLI's "Scanner not found: ..." message contains
    not_connected_message: str  # what POST /scan answers (503) when it isn't plugged in


KNOWN_SCANNERS = (
    KnownScanner(
        id=IX500_ID, name="ScanSnap iX500", usb_id=(0x04C5, 0x132B), entry=IX500,
        not_found_text="No iX500 found",
        not_connected_message="The ScanSnap iX500 isn't connected. Check that it's plugged in and switched on.",
    ),
    KnownScanner(
        id=CANOSCAN_8800F_ID, name="Canon CanoScan 8800F", usb_id=(0x04A9, 0x1901), entry=CANOSCAN_8800F,
        not_found_text="No CanoScan 8800F found",
        not_connected_message="The Canon CanoScan 8800F isn't connected. Check that it's plugged in and switched on.",
    ),
)


def get_scanner(scanner_id: object) -> KnownScanner | None:
    return next((s for s in KNOWN_SCANNERS if s.id == scanner_id), None)


def _read_hex(path: Path) -> int | None:
    try:
        return int(path.read_text().strip(), 16)
    except (OSError, ValueError):
        return None


def usb_ids_from_sysfs(root: Path = SYSFS_USB) -> set[tuple[int, int]]:
    ids: set[tuple[int, int]] = set()
    try:
        devices = list(root.iterdir())
    except OSError:
        return ids
    for device in devices:
        vendor, product = _read_hex(device / "idVendor"), _read_hex(device / "idProduct")
        if vendor is not None and product is not None:
            ids.add((vendor, product))
    return ids


_IOREG_NODE = re.compile(r"\+-o ")
_IOREG_ID = re.compile(r'"(idVendor|idProduct)"\s*=\s*(\d+)')


def usb_ids_from_ioreg(text: str) -> set[tuple[int, int]]:
    """Pairs each USB device node's idVendor and idProduct (decimal) from
    `ioreg -p IOUSB -l` output."""
    ids: set[tuple[int, int]] = set()
    found: dict[str, int] = {}
    for line in text.splitlines():
        if _IOREG_NODE.search(line):
            found = {}
            continue
        match = _IOREG_ID.search(line)
        if match:
            found[match.group(1)] = int(match.group(2))
            if len(found) == 2:
                ids.add((found["idVendor"], found["idProduct"]))
                found = {}
    return ids


def usb_ids() -> set[tuple[int, int]]:
    """(vendor, product) of every USB device present. Never raises."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-p", "IOUSB", "-l", "-w0"], capture_output=True, text=True,
                                 timeout=5, check=True).stdout
            return usb_ids_from_ioreg(out)
        return usb_ids_from_sysfs(SYSFS_USB)
    except Exception:  # noqa: BLE001 - discovery must never raise into the HTTP thread
        return set()


def attached_scanners(ids: set[tuple[int, int]] | None = None) -> list[dict]:
    """The GET /scanners entries of the known scanners that are plugged in."""
    present = usb_ids() if ids is None else ids
    return [copy.deepcopy(s.entry) for s in KNOWN_SCANNERS if s.usb_id in present]
```

- [ ] **Step 4: Run the new tests**

Run: `.venv/bin/python -m pytest tests/test_scanners.py -v`
Expected: all PASS.

- [ ] **Step 5: Make the rest of the suite pretend the iX500 is plugged in**

Task 5 makes `RealBackend` read the USB bus; test machines have no scanner. Append to `tests/conftest.py`:

```python


@pytest.fixture(autouse=True)
def _ix500_attached(monkeypatch):
    """Tests run on machines with no scanner: by default pretend the iX500 is plugged in."""
    monkeypatch.setattr("scanix500.scanners.usb_ids", lambda: {(0x04C5, 0x132B)})
```

(`test_scanners.py` imports `usb_ids` by name at import time, so its tests still reach the real function.)

Run: `.venv/bin/python -m pytest -q`
Expected: 353 + the new tests, all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/scanix500/scanners.py tests/test_scanners.py tests/conftest.py
git commit -m "Scanner catalogue and USB discovery (iX500, CanoScan 8800F)"
```

---

### Task 2: `Profile.scanner` and the `--scanner` CLI option

**Files:**
- Modify: `src/scanix500/menubar/profiles.py` (the `Profile` dataclass)
- Modify: `src/scanix500/menubar/runner.py` (`_build_argv`)
- Test: `tests/menubar/test_profiles.py`, `tests/menubar/test_runner.py`

**Interfaces:**
- Consumes: `IX500_ID` from Task 1.
- Produces: `Profile.scanner: str = IX500_ID` (last field); `_build_argv(profile)` adds `["--scanner", profile.scanner]` only when it isn't `IX500_ID` (task 4's CLI reads the option).

- [ ] **Step 1: Write the failing tests**

Append to `tests/menubar/test_profiles.py`:

```python
def test_profile_scanner_defaults_to_the_ix500_and_old_files_still_load(tmp_path):
    import json
    from scanix500.menubar.profiles import load_profiles, save_profiles

    path = tmp_path / "profiles.json"
    path.write_text(json.dumps([{"name": "Old", "destination": "/d", "duplex": False}]))
    loaded = load_profiles(path)
    assert loaded[0].scanner == "sane:ix500"

    save_profiles(path, [Profile(name="Flat", destination="/d", scanner="sane:canoscan-8800f")])
    assert load_profiles(path)[0].scanner == "sane:canoscan-8800f"
```

(`Profile` is already imported at the top of that file; if the existing tests import it differently, add `from scanix500.menubar.profiles import Profile` to the test.)

Append to `tests/menubar/test_runner.py`:

```python
def test_build_argv_names_the_scanner_only_when_it_is_not_the_ix500():
    from scanix500.menubar.runner import _build_argv
    assert "--scanner" not in _build_argv(Profile(name="p", destination="/d"))
    argv = _build_argv(Profile(name="p", destination="/d", scanner="sane:canoscan-8800f", resolution=600))
    assert argv[1:] == ["/d", "--resolution", "600", "--scanner", "sane:canoscan-8800f"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/menubar/test_profiles.py tests/menubar/test_runner.py -q`
Expected: the two new tests FAIL (`unexpected keyword argument 'scanner'`).

- [ ] **Step 3: Implement**

In `src/scanix500/menubar/profiles.py` add the import and the field:

```python
from scanix500.i18n import t
from scanix500.scanners import IX500_ID
```

```python
    resolution: int = 300
    keep_blank_pages: bool = False
    scanner: str = IX500_ID
```

In `src/scanix500/menubar/runner.py` add the import `from scanix500.scanners import IX500_ID` (next to the other `scanix500` imports) and, in `_build_argv`, after the `keep_blank_pages` block and before `return argv`:

```python
    if profile.scanner != IX500_ID:
        argv += ["--scanner", profile.scanner]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/menubar -q`
Expected: all PASS. If an existing test compares a whole `asdict(Profile(...))`, add `"scanner": "sane:ix500"` to its expected dict.

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/menubar/profiles.py src/scanix500/menubar/runner.py tests/menubar
git commit -m "Profile.scanner and the CLI --scanner argument"
```

---

### Task 3: `PySaneFlatbed` (one-frame capture for the 8800F)

**Files:**
- Modify: `src/scanix500/capture.py`
- Test: `tests/test_capture.py`

**Interfaces:**
- Consumes: `SANE_MODES`, `DEFAULT_RESOLUTION_DPI`, `ScannerNotFoundError`, `ScannerSetupError` (already in `capture.py`).
- Produces:
  - `find_canoscan_device(devices: list[tuple]) -> str | None` (the SANE device name of the 8800F or `None`)
  - `class PySaneFlatbed(*, color_mode: str = "color", resolution: int = DEFAULT_RESOLUTION_DPI)` implementing `SaneDevice`: `start()`, `read_frame()` (one PIL image with `info["dpi"]`, then `StopIteration`), `multi_feed_detected() -> False`. Raises `ScannerNotFoundError("No CanoScan 8800F found via SANE pixma backend")` when absent (the text contains `KnownScanner.not_found_text`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_capture.py`:

```python
def test_find_canoscan_device_matches_the_pixma_8800f():
    from scanix500.capture import find_canoscan_device
    devices = [
        ("fujitsu:ScanSnap iX500:1203900", "FUJITSU", "ScanSnap iX500", "scanner"),
        ("pixma:04A91901", "CANON", "Canoscan 8800F", "multi-function peripheral"),
    ]
    assert find_canoscan_device(devices) == "pixma:04A91901"


def test_find_canoscan_device_ignores_other_canon_scanners_and_other_backends():
    from scanix500.capture import find_canoscan_device
    devices = [
        ("pixma:04A91234", "CANON", "Canoscan LiDE 400", "multi-function peripheral"),
        ("test:0", "Noname", "8800F lookalike", "virtual device"),
    ]
    assert find_canoscan_device(devices) is None


class _FakeFlatbedDev:
    resolution = 300

    def __init__(self, image):
        self._image = image
        self.scans = 0

    def snap(self):
        raise AssertionError("snap() without start() never returns on real hardware")

    def scan(self):
        self.scans += 1
        return self._image


def _flatbed_with(image, resolution=300):
    # Skips __init__, which needs libsane and a real scanner (verified manually on hardware).
    from scanix500.capture import PySaneFlatbed
    device = PySaneFlatbed.__new__(PySaneFlatbed)
    device._dev = _FakeFlatbedDev(image)
    device._resolution = resolution
    device._pending = False
    return device


def test_flatbed_returns_one_frame_with_its_dpi_then_stops():
    device = _flatbed_with(_img("glass"), resolution=600)
    device.start()
    frame = device.read_frame()
    assert frame.info["tag"] == "glass" and frame.info["dpi"] == (600, 600)
    with pytest.raises(StopIteration):
        device.read_frame()
    assert device._dev.scans == 1 and device.multi_feed_detected() is False


def test_flatbed_scans_again_after_a_new_start():
    device = _flatbed_with(_img("glass"))
    device.start(); device.read_frame()
    device.start(); device.read_frame()
    assert device._dev.scans == 2


def test_capture_pages_gives_one_page_for_a_flatbed():
    device = _flatbed_with(_img("glass"))
    pages = capture_pages(device, duplex=False)
    assert len(pages) == 1 and pages[0].back is None and pages[0].front.info["tag"] == "glass"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_capture.py -q`
Expected: the new tests FAIL (`ImportError: cannot import name 'find_canoscan_device'` / `PySaneFlatbed`).

- [ ] **Step 3: Implement**

In `src/scanix500/capture.py`, after `find_fujitsu_device`, add:

```python
def find_canoscan_device(devices: list[tuple]) -> str | None:
    """Given the (name, vendor, model, type) tuples sane.get_devices()
    returns, finds the CanoScan 8800F (SANE's pixma backend) and returns its
    device name, or None if not found."""
    for d in devices:
        if d[0].startswith("pixma:") and "8800f" in d[2].lower():
            return d[0]
    return None
```

and at the end of the file (after `PySaneDevice`):

```python
class PySaneFlatbed:
    """The CanoScan 8800F's glass through python-sane (SANE's pixma
    backend): one A4 page per scan, then StopIteration, so capture_pages(
    duplex=False) returns one page. Imports `sane` lazily like PySaneDevice;
    only real hardware runs the SANE calls (verified manually, see the plan's
    checklist)."""

    def __init__(self, *, color_mode: str = "color", resolution: int = DEFAULT_RESOLUTION_DPI):
        try:
            import sane
        except ImportError as e:
            raise ScannerNotFoundError(
                "python-sane is not installed — see README for install instructions"
            ) from e

        try:
            sane.init()
        except Exception as e:
            raise ScannerNotFoundError(f"Failed to initialise SANE: {e}") from e

        try:
            devices = sane.get_devices()
        except Exception as e:
            raise ScannerNotFoundError(f"Failed to enumerate SANE devices: {e}") from e

        device_name = find_canoscan_device(devices)
        if device_name is None:
            raise ScannerNotFoundError("No CanoScan 8800F found via SANE pixma backend")

        try:
            self._dev = sane.open(device_name)
        except Exception as e:
            raise ScannerNotFoundError(f"Failed to open SANE device: {e}") from e

        try:
            # pixma: "Set source before mode and resolution" -- setting the
            # source resets both to their automatic values. The scan area is
            # left at the driver's default, the whole glass (216 x 297 mm).
            self._dev.source = "Flatbed"
            self._dev.mode = SANE_MODES[color_mode]
            self._dev.resolution = resolution
        except Exception as e:
            raise ScannerSetupError(f"The scanner refused a setting: {e}") from e

        self._resolution = resolution
        self._pending = False

    def start(self) -> None:
        self._pending = True

    def read_frame(self):
        if not self._pending:
            raise StopIteration
        self._pending = False
        frame = self._dev.scan()  # start()+snap(); snap() alone never returns
        # python-sane never sets .info["dpi"]; pdf_builder needs it for the
        # PDF page geometry (see PySaneDevice.read_frame).
        frame.info["dpi"] = (self._resolution, self._resolution)
        return frame

    def multi_feed_detected(self) -> bool:
        return False
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_capture.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/capture.py tests/test_capture.py
git commit -m "PySaneFlatbed: one-frame capture for the CanoScan 8800F"
```

---

### Task 4: CLI `--scanner` and the pass-through flatbed pipeline

**Files:**
- Modify: `src/scanix500/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `PySaneFlatbed`, `KNOWN_SCANNERS`, `IX500_ID`, `CANOSCAN_8800F_ID` from Tasks 1 and 3.
- Produces: `scanix500 <dest> --scanner {sane:ix500,sane:canoscan-8800f}` (default `sane:ix500`). With the 8800F: one A4 page, no trim/snap/blank filter, `--mode bw` thresholded, OCR as usual.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def _fake_ocr(cmd, check, capture_output):
    Path(cmd[2]).write_bytes(Path(cmd[1]).read_bytes())


def test_flatbed_scan_uses_the_flatbed_device_and_skips_trim_snap_and_blank_filter(tmp_path):
    blank_page = PagePair(Image.new("L", (100, 100), color=255))  # a blank page must still come out
    with patch("scanix500.cli.PySaneFlatbed") as flatbed, patch("scanix500.cli.PySaneDevice") as feeder, patch(
        "scanix500.cli.capture_pages", return_value=[blank_page]
    ) as capture, patch("scanix500.cli.trim_background", side_effect=AssertionError("trimmed")), patch(
        "scanix500.cli.snap_to_standard_size", side_effect=AssertionError("snapped")
    ), patch("scanix500.cli.filter_pages", side_effect=AssertionError("filtered")), patch(
        "scanix500.pdf_builder.subprocess.run", side_effect=_fake_ocr
    ):
        exit_code = main([str(tmp_path), "--scanner", "sane:canoscan-8800f", "--mode", "gray", "--resolution", "600"])

    assert exit_code == 0
    flatbed.assert_called_once_with(color_mode="gray", resolution=600)
    feeder.assert_not_called()
    assert capture.call_args.kwargs == {"duplex": False}
    assert len(PdfReader(next(tmp_path.glob("scan_*.pdf"))).pages) == 1


def test_flatbed_black_and_white_is_thresholded(tmp_path):
    page = PagePair(_content_image())
    with patch("scanix500.cli.PySaneFlatbed"), patch("scanix500.cli.capture_pages", return_value=[page]), patch(
        "scanix500.cli.to_black_and_white", side_effect=lambda im: im.convert("1")
    ) as bw, patch("scanix500.pdf_builder.subprocess.run", side_effect=_fake_ocr):
        assert main([str(tmp_path), "--scanner", "sane:canoscan-8800f", "--mode", "bw"]) == 0
    assert bw.call_count == 1


def test_the_default_scanner_is_still_the_ix500(tmp_path):
    with patch("scanix500.cli.PySaneFlatbed") as flatbed, patch("scanix500.cli.PySaneDevice"), patch(
        "scanix500.cli.capture_pages", return_value=[PagePair(_content_image(), _content_image())]
    ), patch("scanix500.pdf_builder.subprocess.run", side_effect=_fake_ocr):
        assert main([str(tmp_path)]) == 0
    flatbed.assert_not_called()


def test_flatbed_not_found_reports_the_scanner(tmp_path, capsys):
    from scanix500.errors import ScannerNotFoundError
    with patch("scanix500.cli.PySaneFlatbed", side_effect=ScannerNotFoundError("No CanoScan 8800F found via SANE pixma backend")):
        assert main([str(tmp_path), "--scanner", "sane:canoscan-8800f"]) == 1
    assert "Scanner not found: No CanoScan 8800F found" in capsys.readouterr().err


def test_an_unknown_scanner_id_is_refused(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        main([str(tmp_path), "--scanner", "sane:nope"])
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: new tests FAIL (`unrecognized arguments: --scanner` / no attribute `PySaneFlatbed`).

- [ ] **Step 3: Implement**

In `src/scanix500/cli.py`:

Imports: replace the `capture` import line and add the scanners import:

```python
from scanix500.capture import PagePair, PySaneDevice, PySaneFlatbed, capture_pages
```

```python
from scanix500.router import compute_output_paths
from scanix500.scanners import CANOSCAN_8800F_ID, IX500_ID, KNOWN_SCANNERS
from scanix500.snap import snap_to_standard_size
```

In `parse_args`, after `--keep-blank-pages`:

```python
    parser.add_argument("--scanner", choices=[s.id for s in KNOWN_SCANNERS], default=IX500_ID,
                        help="which scanner to use (the CanoScan 8800F scans one flatbed page)")
```

In `main`, replace the first `try:` body lines

```python
        device = PySaneDevice(duplex=not args.simplex, color_mode=args.mode, resolution=args.resolution)
        pages = capture_pages(device, duplex=not args.simplex)
```

with

```python
        if flatbed:
            device = PySaneFlatbed(color_mode=args.mode, resolution=args.resolution)
            pages = capture_pages(device, duplex=False)
        else:
            device = PySaneDevice(duplex=not args.simplex, color_mode=args.mode, resolution=args.resolution)
            pages = capture_pages(device, duplex=not args.simplex)
```

and put `flatbed = args.scanner == CANOSCAN_8800F_ID` right after `args = parse_args(argv)`.

Then replace the block from `# Trim the scanner's background from the sides first, ...` through the end of the `if args.skip_blank_filter: ... else: partitions = filter_pages(...)` statement with:

```python
    if flatbed:
        # One page straight from the glass: the lid is white, so there's no
        # background to trim, the A4 scan area is the page, and one page
        # needs no blank check.
        partitions = [[pair.front for pair in pages]]
    else:
        # Trim the scanner's background from the sides first, so an A5 sheet
        # comes out at A5 width and the blank check only sees the paper; then a
        # page within a few mm of a standard size gets exactly that size.
        def fit(image):
            return snap_to_standard_size(trim_background(image))

        pages = [PagePair(fit(p.front), None if p.back is None else fit(p.back)) for p in pages]

        if args.skip_blank_filter:
            partitions = [
                [img for pair in pages for img in (pair.front, pair.back) if img is not None]
            ]
        else:
            partitions = filter_pages(
                pages, split_on_blank=args.split_on_blank, drop_blank_pages=not args.keep_blank_pages
            )
```

(The `if not partitions:` check, the bw conversion and the PDF building below stay as they are.)

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: all PASS (existing iX500 tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/cli.py tests/test_cli.py
git commit -m "CLI: --scanner, and the pass-through flatbed pipeline for the 8800F"
```

---

### Task 5: Protocol and bridge use the catalogue

**Files:**
- Modify: `src/scanix500/menubar/protocol_v2.py`
- Modify: `src/scanix500/menubar/bridge.py`
- Test: `tests/menubar/test_protocol_v2.py`, `tests/menubar/test_bridge.py`

**Interfaces:**
- Consumes: `attached_scanners`, `get_scanner`, `KnownScanner`, `IX500`, `IX500_ID` (Task 1); `Profile.scanner` (Task 2).
- Produces:
  - `profile_for(settings: dict, destination: str, scanner: str = IX500_ID) -> Profile` (sets `Profile.scanner`)
  - `response_from_result(result: ScanResult, scanner_id: str = IX500_ID) -> tuple[int, dict]` (503 texts per scanner)
  - `RealBackend.scanners()` = the attached scanners only (OCR languages filtered as before)
  - `POST /scan` for a known but unplugged scanner: validated against its catalogue entry, then **503** with its `not_connected_message`; an unknown id stays 404.
  - `protocol_v2` still exports `IX500` and `NOT_CONNECTED_MESSAGE` (existing tests import them).

- [ ] **Step 1: Write the failing tests**

Append to `tests/menubar/test_protocol_v2.py`:

```python
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
```

(`profile_for`, `response_from_result`, `ScanResult`, `error_body` are already imported in that file; add any that are missing to its import block.)

Append to `tests/menubar/test_bridge.py`:

```python
BOTH = {(0x04C5, 0x132B), (0x04A9, 0x1901)}
ONLY_8800F = {(0x04A9, 0x1901)}


def test_scanners_lists_what_is_plugged_in(v2_server, monkeypatch):
    base, _ = v2_server
    for ids, expected in [(BOTH, ["sane:ix500", "sane:canoscan-8800f"]), (ONLY_8800F, ["sane:canoscan-8800f"]), (set(), [])]:
        monkeypatch.setattr("scanix500.scanners.usb_ids", lambda ids=ids: ids)
        status, body, _ = _request(base, "GET", "/scanners")
        assert status == 200 and [s["id"] for s in body["scanners"]] == expected


def test_scan_on_the_8800f_runs_a_profile_for_it(v2_server, monkeypatch):
    base, trigger = v2_server
    monkeypatch.setattr("scanix500.scanners.usb_ids", lambda: BOTH)
    _request(base, "POST", "/scan", {"scanner": "sane:canoscan-8800f", "colorMode": "gray", "resolution": 150})
    p = trigger.called_with
    assert (p.scanner, p.duplex, p.color_mode, p.resolution) == ("sane:canoscan-8800f", False, "gray", 150)


def test_8800f_refuses_feeder_extras_and_duplex(v2_server, monkeypatch):
    base, _ = v2_server
    monkeypatch.setattr("scanix500.scanners.usb_ids", lambda: BOTH)
    status, body, _ = _request(base, "POST", "/scan", {"scanner": "sane:canoscan-8800f", "extras": {"splitOnBlank": True}})
    assert status == 422
    status, body, _ = _request(base, "POST", "/scan", {"scanner": "sane:canoscan-8800f", "duplex": True})
    assert status == 422
    status, body, _ = _request(base, "POST", "/scan", {"scanner": "sane:canoscan-8800f", "source": "feeder"})
    assert status == 422


def test_scan_on_an_unplugged_known_scanner_is_503(v2_server):
    base, trigger = v2_server  # only the iX500 is attached (conftest)
    status, body, _ = _request(base, "POST", "/scan", {"scanner": "sane:canoscan-8800f"})
    assert status == 503 and body["message"].startswith("The Canon CanoScan 8800F isn't connected")
    assert body["error"] == body["message"] and trigger.called_with is None


def test_unplugged_known_scanner_is_still_validated_first(v2_server):
    base, _ = v2_server
    status, _, _ = _request(base, "POST", "/scan", {"scanner": "sane:canoscan-8800f", "resolution": 1200})
    assert status == 422
    status, _, _ = _request(base, "POST", "/scan", {"scanner": "sane:canoscan-8800f", "duplex": "yes"})
    assert status == 400


def test_scan_on_an_unknown_scanner_is_still_404(v2_server):
    base, _ = v2_server
    status, _, _ = _request(base, "POST", "/scan", {"scanner": "sane:nope"})
    assert status == 404
```

(`FakeScanTrigger.called_with` starts as `None`, so "no scan was started" is `trigger.called_with is None`.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/menubar/test_protocol_v2.py tests/menubar/test_bridge.py -q`
Expected: the new tests FAIL (`profile_for() takes 2 positional arguments`, scanners list wrong, 404 instead of 503).

- [ ] **Step 3: Implement `protocol_v2.py`**

Replace the `IX500 = {...}` block, its two comment lines above it, and the `NOT_CONNECTED_MESSAGE = ...` line. The imports become:

```python
from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult
from scanix500.scanners import IX500, IX500_ID, get_scanner  # noqa: F401 - IX500 is re-exported
```

and where `IX500` and `NOT_CONNECTED_MESSAGE` were:

```python
EMPTY_FEEDER_MESSAGE = "No paper in the feeder."
ALL_BLANK_MESSAGE = "Every page was blank, so no PDF was made."
NOT_CONNECTED_MESSAGE = get_scanner(IX500_ID).not_connected_message
```

(`EMPTY_FEEDER_MESSAGE` and `ALL_BLANK_MESSAGE` stay exactly where they are; only the dict and the old message line are removed.)

`profile_for`: new signature and field:

```python
def profile_for(settings: dict, destination: str, scanner: str = IX500_ID) -> Profile:
```
and in the `Profile(...)` call add `scanner=scanner,` after `keep_blank_pages=...`.

`response_from_result`: new signature and the not-found branch:

```python
def response_from_result(result: ScanResult, scanner_id: str = IX500_ID) -> tuple[int, dict]:
    known = get_scanner(scanner_id) or get_scanner(IX500_ID)
```
(first lines of the function), and replace the `if result.message.startswith("Scanner not found"):` block with:

```python
    prefix = "Scanner not found: "
    if result.message.startswith("Scanner not found"):
        if known.not_found_text in result.message:
            return 503, error_body(known.not_connected_message)
        detail = _first_part(result.message[len(prefix):] if result.message.startswith(prefix) else result.message)
        return 503, error_body(f"The {known.name} can't be used right now: {detail}")
```

- [ ] **Step 4: Implement `bridge.py`**

Imports: change the protocol_v2 import line to drop `IX500`, and add the scanners import:

```python
from scanix500.menubar.protocol_v2 import ProtocolError, error_body, profile_for, response_from_result, validate
from scanix500.menubar.runner import ScanResult
from scanix500.scanners import KnownScanner, attached_scanners, get_scanner
```

Add above `class V2Backend`:

```python
def _absent_scanner(body: object, attached: list[dict]) -> KnownScanner | None:
    """The catalogue scanner a scan request names when it's known but not
    plugged in (so the answer is 503 "not connected", not 404)."""
    scanner_id = body.get("scanner") if isinstance(body, dict) else None
    if not isinstance(scanner_id, str) or any(s["id"] == scanner_id for s in attached):
        return None
    return get_scanner(scanner_id)
```

In `RealBackend`, replace `scanners()`:

```python
    def scanners(self) -> list[dict]:
        return [self._with_ocr(entry) for entry in attached_scanners()]

    def _with_ocr(self, entry: dict) -> dict:
        entry = dict(entry)
        if self._no_ocr:
            entry.pop("ocr", None)
        else:
            installed = installed_ocr_languages()
            if installed is not None:
                entry["ocr"] = {"languages": [c for c in entry["ocr"]["languages"] if c in installed]}
        return entry
```

and in `scan()` change the first two lines of the body:

```python
        result = self._trigger.trigger(profile_for(settings, self._get_destination(), scanner["id"]))
        status, body = response_from_result(result, scanner["id"])
```

In `_handle_v2_scan`, replace

```python
            try:
                scanner, settings = validate(body, backend.scanners())
            except ProtocolError as e:
```
with

```python
            scanners = backend.scanners()
            absent = _absent_scanner(body, scanners)
            try:
                # A known scanner that isn't plugged in is validated against its
                # catalogue entry first, then refused as "not connected".
                scanner, settings = validate(body, (scanners + [absent.entry]) if absent else scanners)
                if absent:
                    raise ProtocolError(503, absent.not_connected_message)
            except ProtocolError as e:
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS, including the untouched existing bridge/protocol tests (`test_scanners_lists_the_ix500`, the OCR language tests, the 503 `NOT_CONNECTED_MESSAGE` test).

- [ ] **Step 6: Run the protocol conformance suite**

Run: `scripts/run-conformance.sh`
Expected: dossiary-scan-helper's suite passes against the fake backend (51 tests, as in AGENTS.md). It never touches the real catalogue.

- [ ] **Step 7: Commit**

```bash
git add src/scanix500/menubar/protocol_v2.py src/scanix500/menubar/bridge.py tests/menubar
git commit -m "GET /scanners lists what is plugged in; the scan carries its scanner; 503 per scanner"
```

---

### Task 6: The 8800F's buttons on the Mac

**Files:**
- Already written by the controller, uncommitted: `scripts/probe-pixma-buttons.py` (the manual probe; commit it with this task) and the spec's section 5 (the verified button table).
- Modify: `src/scanix500/menubar/button_watcher.py`
- Modify: `src/scanix500/menubar/app.py` (`__init__` state and `_poll_button`)
- Test: `tests/menubar/test_button_watcher.py` (new), `tests/menubar/test_app.py`

**Verified on the real 8800F (2026-10-24, see the spec's section 5):** polled the way the app polls (open, read, close), SANE's `target` option reads the pressed button's number for about one poll and 0 otherwise: 1 COPY, 2 PHOTO/FILM, 3 E-MAIL, 4 COLOR, 5 BLACK & WHITE, 6 CUSTOM, 7 FINISH. `button-update` cannot be set through python-sane, and `button-1` blips to 1 about every 12 s while idle, so **only `target` is read, and `button-update` is never called**.

**Interfaces:**
- Consumes: `find_canoscan_device`, `find_fujitsu_device` (capture.py), `IX500_ID`, `CANOSCAN_8800F_ID` (Task 1), `Profile.scanner` (Task 2).
- Produces:
  - `resolve_device_name(scanner_id: str = IX500_ID) -> str | None` (the default keeps every existing caller working)
  - `PIXMA_BUTTONS: dict[int, dict]` = `{4: {"color_mode": "color"}, 5: {"color_mode": "bw"}, 6: {}}`
  - `_pixma_overrides(values: dict[str, int]) -> dict | None` (pure)
  - `pixma_button_overrides(device_name: str) -> dict | None` (hardware; never raises)
  - `ix500_button_overrides(device_name: str) -> dict | None` (`{}` when the iX500's button is pressed, else `None`)
  - `BUTTON_READERS: dict[str, Callable[[str], dict | None]]` (scanner id -> reader; `None` = no press, a dict = Profile overrides, `{}` = none)
  - the app polls each scanner in `BUTTON_READERS` and starts a scan with `dataclasses.replace(profile, scanner=<id>, **overrides)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/menubar/test_button_watcher.py`:

```python
import sys

import pytest

from scanix500.menubar import button_watcher
from scanix500.menubar.button_watcher import (
    BUTTON_READERS, PIXMA_BUTTONS, _pixma_overrides, ix500_button_overrides, resolve_device_name,
)
from scanix500.scanners import CANOSCAN_8800F_ID, IX500_ID

DEVICES = [
    ("fujitsu:ScanSnap iX500:1203900", "FUJITSU", "ScanSnap iX500", "scanner"),
    ("pixma:04A91901", "CANON", "Canoscan 8800F", "multi-function peripheral"),
]


class _FakeSane:
    def init(self):
        pass

    def get_devices(self):
        return DEVICES


def test_resolve_finds_each_scanners_device(monkeypatch):
    monkeypatch.setitem(sys.modules, "sane", _FakeSane())
    assert resolve_device_name() == "fujitsu:ScanSnap iX500:1203900"  # the default is the iX500
    assert resolve_device_name(IX500_ID) == "fujitsu:ScanSnap iX500:1203900"
    assert resolve_device_name(CANOSCAN_8800F_ID) == "pixma:04A91901"


def test_resolve_returns_none_for_an_unknown_scanner_or_without_sane(monkeypatch):
    monkeypatch.setitem(sys.modules, "sane", _FakeSane())
    assert resolve_device_name("sane:nope") is None
    monkeypatch.setitem(sys.modules, "sane", None)  # import sane -> ImportError
    assert resolve_device_name(CANOSCAN_8800F_ID) is None


# Measured on the real 8800F (see the spec's section 5): `target` is the pressed
# button's number for about one poll and 0 otherwise.
@pytest.mark.parametrize("target, expected", [
    (0, None),
    (1, None),  # COPY
    (2, None),  # PHOTO/FILM
    (3, None),  # E-MAIL
    (4, {"color_mode": "color"}),  # COLOR
    (5, {"color_mode": "bw"}),  # BLACK & WHITE
    (6, {}),  # CUSTOM: the profile as it is
    (7, None),  # FINISH
    (8, None),
])
def test_pixma_buttons(target, expected):
    # button-1 blips to 1 while idle; it must not matter.
    assert _pixma_overrides({"button-1": 1, "button-2": 0, "target": target}) == expected


def test_pixma_overrides_without_a_target_is_no_press():
    assert _pixma_overrides({}) is None


def test_pixma_overrides_returns_a_copy():
    _pixma_overrides({"target": 4})["color_mode"] = "gray"
    assert PIXMA_BUTTONS[4] == {"color_mode": "color"}


def test_an_ix500_press_means_no_overrides(monkeypatch):
    monkeypatch.setattr(button_watcher, "button_pressed", lambda dev: True)
    assert ix500_button_overrides("fujitsu:1") == {}
    monkeypatch.setattr(button_watcher, "button_pressed", lambda dev: False)
    assert ix500_button_overrides("fujitsu:1") is None


def test_each_button_scanner_has_a_reader():
    assert BUTTON_READERS == {IX500_ID: ix500_button_overrides,
                              CANOSCAN_8800F_ID: button_watcher.pixma_button_overrides}


def test_pixma_button_overrides_never_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "sane", None)
    assert button_watcher.pixma_button_overrides("pixma:04A91901") is None
```

Append to `tests/menubar/test_app.py`:

```python
def _poller_app(profile, started):
    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)
    app._scanning = False
    app._button_devices = {}
    app._ticks_since_resolve_attempt = 0
    app.profiles = [profile]
    app._start_scan = lambda p, on_done=None: started.append(p) or True
    return app


def _hardware_profile(**changes):
    from scanix500.menubar.profiles import HARDWARE_BUTTON_PROFILE_NAME
    return Profile(name=HARDWARE_BUTTON_PROFILE_NAME, destination="/tmp/scans", **changes)


def test_poll_button_scans_with_the_scanner_and_mode_of_the_pressed_button():
    from scanix500.menubar import app as app_module

    started = []
    app = _poller_app(_hardware_profile(), started)
    readers = {"sane:ix500": lambda dev: None,
               "sane:canoscan-8800f": lambda dev: {"color_mode": "bw"} if dev == "pixma:1" else None}
    names = {"sane:ix500": "fujitsu:1", "sane:canoscan-8800f": "pixma:1"}
    with patch.object(app_module, "BUTTON_READERS", readers), patch.object(
        app_module, "resolve_device_name", side_effect=lambda sid: names[sid]
    ):
        app._poll_button(None)

    assert [(p.scanner, p.color_mode, p.destination) for p in started] == [
        ("sane:canoscan-8800f", "bw", "/tmp/scans")]


def test_an_ix500_press_keeps_the_profiles_own_mode():
    from scanix500.menubar import app as app_module

    started = []
    app = _poller_app(_hardware_profile(color_mode="gray"), started)
    with patch.object(app_module, "BUTTON_READERS", {"sane:ix500": lambda dev: {}}), patch.object(
        app_module, "resolve_device_name", return_value="fujitsu:1"
    ):
        app._poll_button(None)

    assert [(p.scanner, p.color_mode) for p in started] == [("sane:ix500", "gray")]


def test_poll_button_does_nothing_while_scanning_or_when_nothing_is_pressed():
    from scanix500.menubar import app as app_module

    started = []
    app = _poller_app(_hardware_profile(), started)
    readers = {"sane:ix500": lambda dev: {}}
    with patch.object(app_module, "BUTTON_READERS", readers), patch.object(
        app_module, "resolve_device_name", return_value="fujitsu:1"
    ):
        app._scanning = True
        app._poll_button(None)
        assert started == []
        app._scanning = False
        readers["sane:ix500"] = lambda dev: None
        app._poll_button(None)
        assert started == []


def test_poll_button_retries_an_unresolved_scanner_only_every_nth_tick():
    from scanix500.menubar import app as app_module

    app = _poller_app(_hardware_profile(), [])
    with patch.object(app_module, "BUTTON_READERS", {"sane:canoscan-8800f": lambda dev: None}), patch.object(
        app_module, "resolve_device_name", return_value=None
    ) as resolve:
        for _ in range(app_module.RESOLVE_RETRY_TICKS + 1):
            app._poll_button(None)
    assert resolve.call_count == 2  # tick 0 and tick RESOLVE_RETRY_TICKS
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/menubar/test_button_watcher.py tests/menubar/test_app.py -q`
Expected: FAIL (`cannot import name 'BUTTON_READERS'`; `_poll_button` still uses `_button_device_name`).

- [ ] **Step 3: Implement `button_watcher.py`**

Replace the imports and `resolve_device_name` at the top of `src/scanix500/menubar/button_watcher.py`:

```python
from __future__ import annotations

from collections.abc import Callable

from scanix500.capture import find_canoscan_device, find_fujitsu_device
from scanix500.scanners import CANOSCAN_8800F_ID, IX500_ID

_FINDERS = {IX500_ID: find_fujitsu_device, CANOSCAN_8800F_ID: find_canoscan_device}


def resolve_device_name(scanner_id: str = IX500_ID) -> str | None:
    """Finds a scanner's SANE device name, or None if it can't be found
    right now (not installed, not plugged in, transient failure, unknown
    scanner). Safe to call repeatedly — never raises."""
    finder = _FINDERS.get(scanner_id)
    if finder is None:
        return None
    try:
        import sane
    except ImportError:
        return None
    try:
        sane.init()
        devices = sane.get_devices()
    except Exception:
        return None
    return finder(devices)
```

(`button_pressed` stays exactly as it is.) Append at the end of the file:

```python
# The 8800F's buttons (measured on the real scanner, see the spec's section 5):
# SANE's `target` option reads the pressed button's number for about one poll,
# 0 otherwise. 1 COPY, 2 PHOTO/FILM, 3 E-MAIL and 7 FINISH do nothing; the three
# PDF scan buttons start a scan, COLOR and BLACK & WHITE choosing the mode and
# CUSTOM keeping the profile as it is.
PIXMA_BUTTONS: dict[int, dict] = {4: {"color_mode": "color"}, 5: {"color_mode": "bw"}, 6: {}}


def _pixma_overrides(values: dict[str, int]) -> dict | None:
    """The Profile overrides the pressed button asks for ({} = none), or None
    when no scan button is pressed. Only `target` counts: `button-1` blips to 1
    about every 12 s while the scanner is idle."""
    found = PIXMA_BUTTONS.get(values.get("target", 0))
    return None if found is None else dict(found)


def pixma_button_overrides(device_name: str) -> dict | None:
    """Opens the 8800F, reads its `target` option and closes it again.
    Returns None on any failure rather than raising, like button_pressed().
    button-update is never called: python-sane can't set a button option, and
    a fresh open sees the current state."""
    try:
        import sane

        sane.init()
        dev = sane.open(device_name)
        try:
            opts = {o[1]: o[0] for o in dev.get_options()}  # fresh descriptors, as in button_pressed()
            return _pixma_overrides({"target": dev.dev.get_option(opts["target"])})
        finally:
            dev.close()
    except Exception:
        return None


def ix500_button_overrides(device_name: str) -> dict | None:
    """The iX500 has one Scan button: pressed means a scan with the profile as it is."""
    return {} if button_pressed(device_name) else None


# Which function reads each scanner's buttons. The Mac app polls all of them.
BUTTON_READERS: dict[str, Callable[[str], dict | None]] = {
    IX500_ID: ix500_button_overrides,
    CANOSCAN_8800F_ID: pixma_button_overrides,
}
```

- [ ] **Step 4: Implement the app's poller**

In `src/scanix500/menubar/app.py`: add `from dataclasses import replace` with the other stdlib imports if it isn't there; change the import to `from scanix500.menubar.button_watcher import BUTTON_READERS, resolve_device_name`; in `__init__` replace `self._button_device_name: str | None = None` with `self._button_devices: dict[str, str | None] = {}`; and replace `_poll_button`:

```python
    def _poll_button(self, _sender):
        # Don't touch any device while a scan is running: run_scan spawns the
        # scanix500 CLI as a subprocess that holds the device open for the
        # whole scan, so polling would mean repeated open attempts against a
        # device another process is actively streaming from -- for no
        # benefit, since _start_scan discards a press detected mid-scan anyway.
        if self._scanning:
            return
        retry = self._ticks_since_resolve_attempt % RESOLVE_RETRY_TICKS == 0
        self._ticks_since_resolve_attempt += 1
        for scanner_id, read_button in BUTTON_READERS.items():
            device_name = self._button_devices.get(scanner_id)
            if device_name is None:
                # Resolving does sane.init() + get_devices() (see RESOLVE_RETRY_TICKS).
                if not retry:
                    continue
                device_name = self._button_devices[scanner_id] = resolve_device_name(scanner_id)
                if device_name is None:
                    continue
            overrides = read_button(device_name)
            if overrides is None:
                continue
            profile = find_profile(self.profiles, HARDWARE_BUTTON_PROFILE_NAME)
            if profile is not None:
                self._start_scan(replace(profile, scanner=scanner_id, **overrides))
            return
```

(The old code's `self._ticks_since_resolve_attempt += 1` happened only while unresolved; now it ticks every poll, which keeps the same ~30 s retry.)

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS. `tests/server/test_runtime.py` still passes (the server imports `resolve_device_name` and `button_pressed` unchanged).

- [ ] **Step 6: Commit** (include the controller's probe script and spec edit)

```bash
git add scripts/probe-pixma-buttons.py src/scanix500/menubar tests/menubar docs/superpowers/specs/2026-10-24-canoscan-8800f-flatbed-design.md
git commit -m "Mac app: the CanoScan 8800F's scan buttons start a flatbed scan"
```

- [ ] **Step 7: Manual hardware check on the Mac** (not automated; the controller does this with the user)

Start the app from the checkout, `.venv/bin/scanix500-menubar`, with the 8800F (and later the iX500) attached. COLOR gives a color scan, BLACK & WHITE a black & white one, CUSTOM the profile's own mode; COPY, PHOTO/FILM, E-MAIL and FINISH do nothing; a press during a scan is ignored; the iX500's button still works with both attached.

---

### Task 7: Docs, version 0.8.0 and the hardware checklist

**Files:**
- Modify: `README.md`, `AGENTS.md`, `docs/superpowers/specs/2026-10-24-canoscan-8800f-flatbed-design.md`
- Modify: `pyproject.toml`, `src/scanix500/__init__.py`, `tests/menubar/test_bridge.py` (version)

- [ ] **Step 1: Correct the spec**

In the spec's "Error handling" section, change "(translated through `t()` like the iX500 message)" to "(English, like the iX500's: the text goes to Dossiary, not to a person at the Mac)".

- [ ] **Step 2: Update AGENTS.md**

In the protocol version 2 bullet, replace "**The iX500 is always listed and never probed**: opening SANE from the HTTP thread would fight a scan or the Scan button, so an unplugged scanner shows up only as a `503` on `POST /scan`." with: "**`GET /scanners` lists what is plugged in, found on the USB bus (`scanners.py`: sysfs on Linux, `ioreg` on macOS) and never by opening SANE**: opening SANE from the HTTP thread would fight a scan or the Scan button. A known scanner that isn't plugged in is validated against its catalogue entry and then refused with `503` on `POST /scan`; an unknown id is `404`."

Add a bullet after the "Pages come out at their paper's size" bullet:

```markdown
- **CanoScan 8800F (0.8.0)** -- spec `docs/superpowers/specs/2026-10-24-canoscan-8800f-flatbed-design.md`, plan `docs/superpowers/plans/2026-10-24-canoscan-8800f-flatbed.md`. A second scanner: flatbed, one A4 page per scan, SANE's `pixma` backend (`PySaneFlatbed`), listed as `sane:canoscan-8800f`. The scanner choice travels `POST /scan` -> `Profile.scanner` -> CLI `--scanner`. Its pages skip trim, snap and the blank filter (white lid, one page). Its buttons work on the Mac only (`pixma_button_overrides`: SANE's `target` option is the pressed button's number, table in the spec; COLOR/BLACK & WHITE/CUSTOM start a scan); the server's button only pairs, for both scanners. Photos (JPEG/TIFF) and film are later sub-projects.
```

- [ ] **Step 3: Update README.md**

Insert this section in `README.md` directly before `## Languages`:

```markdown
## Canon CanoScan 8800F (flatbed)

The CanoScan 8800F works as a second scanner next to the iX500: one A4 page
per scan from the glass, 150/300/600 dpi, color/gray/black & white, with OCR.
It uses SANE's `pixma` backend, which `sane-backends` includes. Scanners are
listed in Dossiary only while they are plugged in (the USB bus is checked, SANE
is not opened), so an unplugged scanner disappears from the picker.

- **On the Mac**, its three PDF scan buttons start a scan with the "Hardware
  Button" profile, like the iX500's button: COLOR scans in color, BLACK & WHITE
  in black & white, CUSTOM uses the profile as it is. COPY, PHOTO/FILM, E-MAIL
  and FINISH do nothing. Feeder settings in that profile (both sides, split on
  blank pages) don't apply to the glass and are ignored.
- **On `scanix500-server`** it appears in Dossiary Scan Helper like any scanner.
  The server's button only pairs computers, for either scanner.
- Its pages are not trimmed, snapped to a paper size or checked for blankness:
  the lid is white and a scan is one page. A smaller sheet leaves white margin.
- Photos, higher resolutions and film are not supported yet.
- On the Raspberry Pi, plug scanners in while it is switched off: hot-plugging
  one has frozen the Pi until its power was cut.
```

- [ ] **Step 4: Bump the version**

Set `version = "0.8.0"` in `pyproject.toml`, `__version__ = "0.8.0"` in `src/scanix500/__init__.py`, and `"version": "0.8.0"` in `tests/menubar/test_bridge.py` (`test_health_is_version_2_and_keeps_the_service_name`; grep the file for `0.7.0` and change every occurrence that is this version assertion).

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md AGENTS.md docs pyproject.toml src/scanix500/__init__.py tests/menubar/test_bridge.py
git commit -m "scanix500 0.8.0: docs and version for the CanoScan 8800F"
```

- [ ] **Step 6: Hardware checklist on node-000** (manual, from the spec's "Testing")

After tagging and installing the `.deb` (the user does this: `git tag v0.8.0`, push the tag, wait for the `deb.yml` run, install the release `.deb` on the Pi; with the Pi **switched off** while the scanners are plugged in):

- [ ] `GET /scanners` (through Dossiary Scan Helper, or `curl -k` with a paired token) lists both scanners when both are attached, and only one when the other is unplugged (unplug with the Pi off, then boot).
- [ ] An 8800F scan through Dossiary Scan Helper: gray 300 dpi with OCR, color 150 dpi, black & white 600 dpi; each comes back as one A4 PDF; the OCR'd PDF has searchable text.
- [ ] The first scan after a service restart (the driver's one-time calibration) works and its duration is noted.
- [ ] An iX500 scan and an 8800F scan one after the other work; a second request during a scan gets 409.
- [ ] `journalctl -u scanix500-server` shows no errors; `vcgencmd get_throttled` is still `0x0` after the scans.

- [ ] **Step 7: Hardware checklist on the Mac** (manual)

- [ ] The same scans through the menu bar app's bridge (`GET /scanners` lists both, scans work).
- [ ] COLOR, BLACK & WHITE and CUSTOM start a scan (color, black & white, the profile's mode); COPY, PHOTO/FILM, E-MAIL and FINISH do nothing; the iX500's button still works with both attached; a press during a scan is ignored.
- [ ] The menu bar stays responsive with the 8800F attached and with it unplugged.
