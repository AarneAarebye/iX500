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
        try:
            back = device.read_frame()
        except StopIteration:
            # Odd frame count: the front of this sheet arrived but its back
            # never did, so the pair can't be completed. Treat it as
            # end-of-batch and keep every complete pair captured so far.
            break
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

        # get_devices() yields (name, vendor, model, type) tuples: d[1] is the
        # vendor ("FUJITSU"), d[2] the model ("ScanSnap iX500").
        fujitsu_devices = [
            d for d in devices if "fujitsu" in d[1].lower() or "ix500" in d[2].lower()
        ]
        if not fujitsu_devices:
            raise ScannerNotFoundError("No iX500 found via SANE fujitsu backend")

        try:
            self._dev = sane.open(fujitsu_devices[0][0])
        except Exception as e:
            raise ScannerNotFoundError(f"Failed to open SANE device: {e}") from e

        self._dev.source = "ADF Duplex"
        self._frames = None

    def start(self) -> None:
        # multi_scan() calls sane_start() per frame internally and raises
        # StopIteration at end-of-ADF, matching capture_pages()'s contract.
        self._frames = self._dev.multi_scan()

    def read_frame(self):
        return next(self._frames)

    def multi_feed_detected(self) -> bool:
        try:
            return bool(self._dev.double_feed_detected)
        except AttributeError:
            return False
