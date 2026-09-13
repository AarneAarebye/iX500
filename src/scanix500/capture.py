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


DEFAULT_RESOLUTION_DPI = 300
DEFAULT_MODE = "Color"
A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297


def find_fujitsu_device(devices: list[tuple]) -> str | None:
    """Given the (name, vendor, model, type) tuples sane.get_devices()
    returns, finds the iX500 and returns its device name, or None if
    not found."""
    for d in devices:
        if "fujitsu" in d[1].lower() or "ix500" in d[2].lower():
            return d[0]
    return None


def capture_pages(device: SaneDevice) -> list[PagePair]:
    device.start()
    pages: list[PagePair] = []
    sheet_index = 0
    while True:
        try:
            front = device.read_frame()
        except StopIteration:
            break
        except Exception:
            # A read failed abnormally mid-batch (e.g. a real jam or a
            # double-feed that aborts the read itself rather than merely
            # setting a flag afterward — confirmed possible on real hardware:
            # the fujitsu backend can return SANE_STATUS_JAMMED from a read).
            # Never let this crash out uncaught and lose partial work.
            raise MultiFeedError(sheet_index, pages)
        try:
            back = device.read_frame()
        except StopIteration:
            # Odd frame count: the front of this sheet arrived but its back
            # never did, so the pair can't be completed. Treat it as
            # end-of-batch and keep every complete pair captured so far.
            break
        except Exception:
            raise MultiFeedError(sheet_index, pages)
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

        device_name = find_fujitsu_device(devices)
        if device_name is None:
            raise ScannerNotFoundError("No iX500 found via SANE fujitsu backend")

        try:
            self._dev = sane.open(device_name)
        except Exception as e:
            raise ScannerNotFoundError(f"Failed to open SANE device: {e}") from e

        self._dev.source = "ADF Duplex"
        self._dev.resolution = DEFAULT_RESOLUTION_DPI
        # The backend's own default is "Lineart" (pure 1-bit black & white,
        # not even grayscale) — confirmed against real hardware: an unset
        # mode silently produced stark B&W output. "Color" matches what a
        # general-purpose document scanner should default to.
        self._dev.mode = DEFAULT_MODE
        # Setting page_width/page_height also syncs the scan-area (br_x/br_y)
        # to match — confirmed against real hardware, no separate call needed.
        self._dev.page_width = A4_WIDTH_MM
        self._dev.page_height = A4_HEIGHT_MM

        # Double-feed detection is OFF by default on this hardware and must
        # be explicitly enabled — confirmed against a real iX500. df-action
        # must be set before df-thickness/df-skew become selectable (they
        # start SANE_CAP_INACTIVE until a df-action value is chosen).
        # df-length is deliberately left disabled: it flags any fed sheet
        # whose length differs from page_height by more than df-diff's
        # threshold, which would false-positive on anything shorter than a
        # full page (receipts, business cards) — thickness/skew don't have
        # that failure mode.
        self._dev.df_action = "Stop"
        self._dev.df_thickness = True
        self._dev.df_skew = True

        self._frames = None

    def start(self) -> None:
        # multi_scan() calls sane_start() per frame internally and raises
        # StopIteration at end-of-ADF, matching capture_pages()'s contract.
        self._frames = self._dev.multi_scan()

    def read_frame(self):
        # python-sane's snap()/multi_scan() build the PIL image via
        # Image.frombuffer() and never set .info["dpi"] — confirmed by
        # reading its source. pdf_builder.py relies on that field for
        # correct PDF page geometry, so stamp the real capture resolution
        # onto every frame here; otherwise every real scan silently falls
        # back to pdf_builder's 200 DPI default regardless of the actual
        # resolution used (verified against real hardware: default 600 DPI
        # scans came out with pages sized for 200 DPI, ~3x oversized).
        frame = next(self._frames)
        frame.info["dpi"] = (self._dev.resolution, self._dev.resolution)
        return frame

    def multi_feed_detected(self) -> bool:
        # `omr_df` ("OMR or double feed detected") is the real, live sensor
        # option on this hardware/backend — confirmed via `sane.get_options()`
        # and `scanimage -A`; it's hidden from `scanimage --help`'s default
        # (non-`-A`) output and was NOT named `double_feed_detected` as
        # originally guessed. Deliberate double-feed attempts (stacked sheets,
        # a folded sheet) did not trigger it in testing — the ADF's separation
        # rollers kept peeling sheets apart cleanly — so this remains
        # unconfirmed by an actual live trigger; the attribute name and
        # activation sequence (df_action="Stop" + df_thickness/df_skew=True
        # in __init__) are confirmed correct against real hardware.
        try:
            return bool(self._dev.omr_df)
        except AttributeError:
            return False
