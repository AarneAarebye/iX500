from pathlib import Path

from scanix500 import scanners
from scanix500.scanners import (
    AREAS, CANOSCAN_8800F, CANOSCAN_8800F_ID, IX500, IX500_ID, KNOWN_SCANNERS, MAX_PIXELS, attached_scanners,
    get_scanner, pixel_count, usb_ids, usb_ids_from_ioreg, usb_ids_from_sysfs,
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
    assert CANOSCAN_8800F["resolutions"] == [150, 300, 600, 1200]
    assert CANOSCAN_8800F["colorModes"] == ["color", "gray", "bw"]
    assert CANOSCAN_8800F["name"] == "Canon CanoScan 8800F"


def test_catalogue_ids_and_usb_ids_are_unique_and_match_their_entries():
    assert len({s.id for s in KNOWN_SCANNERS}) == len(KNOWN_SCANNERS)
    assert len({s.usb_id for s in KNOWN_SCANNERS}) == len(KNOWN_SCANNERS)
    for s in KNOWN_SCANNERS:
        assert s.entry["id"] == s.id and s.entry["name"] == s.name
        assert s.name in s.not_connected_message


def test_areas_table_matches_the_protocol():
    assert AREAS == {"a4": (210, 297, "A4"), "a5": (148, 210, "A5"), "a6": (105, 148, "A6"),
                     "10x15": (100, 150, "10 x 15 cm"), "13x18": (130, 180, "13 x 18 cm")}


def test_pixel_count_rounds_each_side_to_whole_pixels():
    assert pixel_count("a4", 600) == 4961 * 7016
    assert pixel_count("10x15", 1200) == 4724 * 7087
    assert pixel_count("a4", 1200) == 9921 * 14031


def test_only_a4_at_1200_dpi_is_over_the_limit_for_the_8800f():
    over = {(area, dpi) for area in CANOSCAN_8800F["areas"] for dpi in CANOSCAN_8800F["resolutions"]
            if pixel_count(area, dpi) > MAX_PIXELS}
    assert over == {("a4", 1200)}


def test_the_8800f_offers_images_areas_and_1200_dpi():
    assert CANOSCAN_8800F["formats"] == ["pdf", "jpeg", "png"]
    assert CANOSCAN_8800F["areas"] == ["a4", "a5", "a6", "10x15", "13x18"]
    assert CANOSCAN_8800F["resolutions"] == [150, 300, 600, 1200]
    assert "formats" not in IX500 and "areas" not in IX500


def test_every_8800f_area_is_in_the_table():
    assert set(CANOSCAN_8800F["areas"]) <= set(AREAS)
