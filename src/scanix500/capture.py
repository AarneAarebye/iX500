from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from scanix500.errors import MultiFeedError, NoPagesScannedError, ScannerNotFoundError


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
