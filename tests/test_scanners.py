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
