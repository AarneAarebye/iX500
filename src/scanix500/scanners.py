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

# Scan areas (PROTOCOL.md "Formats and areas"): id -> (width mm, height mm, label),
# upright, anchored at the glass's corner mark.
AREAS = {
    "a4": (210, 297, "A4"),
    "a5": (148, 210, "A5"),
    "a6": (105, 148, "A6"),
    "10x15": (100, 150, "10 x 15 cm"),
    "13x18": (130, 180, "13 x 18 cm"),
}

# The size limit of one scan, in pixels. The Pi has about 3.4 GB available and a
# scan peaks near three times its raw size, so 100 megapixels (300 MB raw in color)
# is safe there; A4 at 1200 dpi (139 megapixels) is over it.
MAX_PIXELS = 100_000_000


def pixel_count(area: str, dpi: int) -> int:
    width_mm, height_mm, _ = AREAS[area]
    return round(width_mm / 25.4 * dpi) * round(height_mm / 25.4 * dpi)


# One image or A4 page per scan from the glass; no feeder, so no blank-page extras.
CANOSCAN_8800F = {
    "id": CANOSCAN_8800F_ID,
    "name": "Canon CanoScan 8800F",
    "sources": ["flatbed"],
    "duplex": False,
    "colorModes": ["color", "gray", "bw"],
    "resolutions": [150, 300, 600, 1200],
    "extras": [],
    "formats": ["pdf", "jpeg", "png"],
    "areas": ["a4", "a5", "a6", "10x15", "13x18"],
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
