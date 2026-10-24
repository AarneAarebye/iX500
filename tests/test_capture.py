import pytest
from PIL import Image

from scanix500.capture import PagePair, capture_pages, find_fujitsu_device
from scanix500.errors import MultiFeedError, NoPagesScannedError


def _img(tag):
    im = Image.new("RGB", (2, 2))
    im.info["tag"] = tag
    return im


class FakeSaneDevice:
    def __init__(self, frames, multi_feed_after_sheet=None, fail_at_frame=None):
        self._frames = iter(frames)
        self._multi_feed_after_sheet = multi_feed_after_sheet
        self._fail_at_frame = fail_at_frame
        self._frames_read = 0
        self._sheets_seen = 0
        self.started = False

    def start(self):
        self.started = True

    def read_frame(self):
        self._frames_read += 1
        if self._frames_read == self._fail_at_frame:
            raise RuntimeError("simulated read-time hardware fault")
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


def test_capture_pages_raises_multi_feed_error_with_partial_pages():
    frames = [_img("f1"), _img("b1"), _img("f2"), _img("b2"), _img("f3"), _img("b3")]
    device = FakeSaneDevice(frames, multi_feed_after_sheet=2)

    with pytest.raises(MultiFeedError) as exc_info:
        capture_pages(device)

    err = exc_info.value
    assert err.sheet_index == 2
    assert len(err.pages_captured) == 2


def test_capture_pages_drops_incomplete_trailing_front_frame():
    # 3 frames: one complete sheet, then a front whose back never arrives.
    frames = [_img("f1"), _img("b1"), _img("f2")]
    device = FakeSaneDevice(frames)

    pages = capture_pages(device)

    assert pages == [PagePair(frames[0], frames[1])]


def test_capture_pages_raises_when_adf_is_empty():
    device = FakeSaneDevice(frames=[])

    with pytest.raises(NoPagesScannedError):
        capture_pages(device)


def test_capture_pages_converts_read_failure_to_multi_feed_error():
    # Sheet 1 completes normally; sheet 2's front frame raises a hardware
    # fault (e.g. a real jam/double-feed aborting the read itself, not just
    # setting a flag afterward). Sheet 1 must still be preserved.
    frames = [_img("f1"), _img("b1"), _img("f2"), _img("b2")]
    device = FakeSaneDevice(frames, fail_at_frame=3)

    with pytest.raises(MultiFeedError) as exc_info:
        capture_pages(device)

    err = exc_info.value
    assert err.sheet_index == 1
    assert err.pages_captured == [PagePair(frames[0], frames[1])]


def test_find_fujitsu_device_matches_by_vendor():
    devices = [
        ("epson:net:1.2.3.4", "EPSON", "Perfection V600", "scanner"),
        ("fujitsu:ScanSnap iX500:1203900", "FUJITSU", "ScanSnap iX500", "scanner"),
    ]

    assert find_fujitsu_device(devices) == "fujitsu:ScanSnap iX500:1203900"


def test_find_fujitsu_device_matches_by_model_substring():
    # Vendor string doesn't say "fujitsu", but the model does say "ix500".
    devices = [("some:other:device", "ACME", "Widget iX500 Pro", "scanner")]

    assert find_fujitsu_device(devices) == "some:other:device"


def test_find_fujitsu_device_returns_none_when_not_found():
    devices = [("epson:net:1.2.3.4", "EPSON", "Perfection V600", "scanner")]

    assert find_fujitsu_device(devices) is None


def test_capture_pages_simplex_reads_one_frame_per_sheet():
    device = FakeSaneDevice(frames=[_img("a"), _img("b"), _img("c")])
    pages = capture_pages(device, duplex=False)
    assert len(pages) == 3
    assert all(p.back is None for p in pages)


def test_capture_pages_simplex_jam_keeps_earlier_sheets():
    device = FakeSaneDevice(frames=[_img("a"), _img("b")], fail_at_frame=2)
    with pytest.raises(MultiFeedError) as info:
        capture_pages(device, duplex=False)
    assert len(info.value.pages_captured) == 1


def test_a_failed_read_keeps_the_driver_reason():
    device = FakeSaneDevice(frames=[_img("a"), _img("b"), _img("c"), _img("d")], fail_at_frame=3)
    with pytest.raises(MultiFeedError) as info:
        capture_pages(device)
    assert info.value.reason == "simulated read-time hardware fault"


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
        self.snaps = 0

    def snap(self):
        self.snaps += 1
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
    assert device._dev.snaps == 1 and device.multi_feed_detected() is False


def test_flatbed_scans_again_after_a_new_start():
    device = _flatbed_with(_img("glass"))
    device.start(); device.read_frame()
    device.start(); device.read_frame()
    assert device._dev.snaps == 2


def test_capture_pages_gives_one_page_for_a_flatbed():
    device = _flatbed_with(_img("glass"))
    pages = capture_pages(device, duplex=False)
    assert len(pages) == 1 and pages[0].back is None and pages[0].front.info["tag"] == "glass"
