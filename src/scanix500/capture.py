from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from scanix500.errors import MultiFeedError, NoPagesScannedError, ScannerNotFoundError, ScannerSetupError


@dataclass
class PagePair:
    front: Image.Image
    back: Image.Image | None = None


class SaneDevice(Protocol):
    def start(self) -> None: ...
    def read_frame(self) -> Image.Image: ...
    def multi_feed_detected(self) -> bool: ...


DEFAULT_RESOLUTION_DPI = 300
DEFAULT_MODE = "Color"
# Dossiary's colour modes to SANE's. Black & white is scanned as gray and
# thresholded later (see bw.py).
SANE_MODES = {"color": "Color", "gray": "Gray", "bw": "Gray"}
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


def find_canoscan_device(devices: list[tuple]) -> str | None:
    """Given the (name, vendor, model, type) tuples sane.get_devices()
    returns, finds the CanoScan 8800F (SANE's pixma backend) and returns its
    device name, or None if not found."""
    for d in devices:
        if d[0].startswith("pixma:") and "8800f" in d[2].lower():
            return d[0]
    return None


def _reason(e: Exception) -> str:
    return str(e) or type(e).__name__


def capture_pages(device: SaneDevice, *, duplex: bool = True) -> list[PagePair]:
    device.start()
    pages: list[PagePair] = []
    sheet_index = 0
    while True:
        try:
            front = device.read_frame()
        except StopIteration:
            break
        except Exception as e:
            # A read failed abnormally mid-batch (e.g. a real jam or a
            # double-feed that aborts the read itself rather than merely
            # setting a flag afterward — confirmed possible on real hardware:
            # the fujitsu backend can return SANE_STATUS_JAMMED from a read).
            # Never let this crash out uncaught and lose partial work.
            raise MultiFeedError(sheet_index, pages, reason=_reason(e))
        back = None
        if duplex:
            try:
                back = device.read_frame()
            except StopIteration:
                # Odd frame count: the front of this sheet arrived but its back
                # never did, so the pair can't be completed. Treat it as
                # end-of-batch and keep every complete pair captured so far.
                break
            except Exception as e:
                raise MultiFeedError(sheet_index, pages, reason=_reason(e))
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

    def __init__(
        self,
        *,
        duplex: bool = True,
        color_mode: str = "color",
        resolution: int = DEFAULT_RESOLUTION_DPI,
    ):
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

        try:
            self._apply_settings(duplex, color_mode, resolution)
        except Exception as e:
            raise ScannerSetupError(f"The scanner refused a setting: {e}") from e

        self._frames = None

    def _apply_settings(self, duplex: bool, color_mode: str, resolution: int) -> None:
        self._dev.source = "ADF Duplex" if duplex else "ADF Front"
        self._dev.resolution = resolution
        # The backend's own default is "Lineart" (pure 1-bit black & white,
        # not even grayscale) — confirmed against real hardware: an unset
        # mode silently produced stark B&W output. So the mode is always set
        # explicitly; "color" is the default for a general-purpose document
        # scanner (black & white is scanned as gray, see bw.py).
        self._dev.mode = SANE_MODES[color_mode]
        # Setting page_width/page_height also syncs the scan-area (br_x/br_y)
        # to match — confirmed against real hardware, no separate call needed.
        self._dev.page_width = A4_WIDTH_MM
        self._dev.page_height = A4_HEIGHT_MM
        # The scan area stays A4, but each page comes back at its paper's own
        # size (an A5 sheet as A5, a receipt as a receipt) instead of on an
        # A4 page of background -- what ScanSnap's own software does:
        # - ald: the scanner's sensor ends the page where the paper ends.
        #   Needed because swcrop alone can't find a bottom edge: the ADF
        #   background is as light as white paper (measured 236 vs 224 on a
        #   real A5 scan), so swcrop left the A4 length in place.
        # - swcrop stays off: on a real letter (smooth white paper,
        #   2026-10-11) it cut 13-35 mm of text off the sides; with it off
        #   the same sheet came out 210 x 297 mm. trim.py finds the sides
        #   instead, and snap.py rounds a page to a standard size.
        # - swdeskew stays off: on a real, straight-fed sheet (folded once,
        #   2026-10-11) it rotated the page by about 20 degrees, filling the
        #   corners with flat gray; with it off the same sheet came out
        #   straight. pdf_builder's ocrmypdf --deskew
        #   straightens slight tilts instead.
        # pdf_builder sizes each PDF page from the image and its DPI, so the
        # cropped size carries through. All of these are listed by
        # `scanimage -A` for the iX500 (advanced, default off).
        self._dev.ald = True
        self._dev.swdeskew = False
        self._dev.swcrop = False

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
        frame = self._dev.snap()
        # python-sane never sets .info["dpi"]; pdf_builder needs it for the
        # PDF page geometry (see PySaneDevice.read_frame).
        frame.info["dpi"] = (self._resolution, self._resolution)
        return frame

    def multi_feed_detected(self) -> bool:
        return False
