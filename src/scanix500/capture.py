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
