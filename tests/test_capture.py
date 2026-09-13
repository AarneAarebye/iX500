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


def test_capture_pages_raises_multi_feed_error_with_partial_pages():
    frames = [_img("f1"), _img("b1"), _img("f2"), _img("b2"), _img("f3"), _img("b3")]
    device = FakeSaneDevice(frames, multi_feed_after_sheet=2)

    with pytest.raises(MultiFeedError) as exc_info:
        capture_pages(device)

    err = exc_info.value
    assert err.sheet_index == 2
    assert len(err.pages_captured) == 2
