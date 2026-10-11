from __future__ import annotations

class ScannerNotFoundError(Exception):
    """Raised when the SANE fujitsu backend can't find the iX500."""


class ScannerSetupError(Exception):
    """Raised when SANE refuses a scan setting (source, resolution, mode...)."""


class NoPagesScannedError(Exception):
    """Raised when the ADF was empty and no pages were captured."""


class MultiFeedError(Exception):
    """Raised when the scanner's multi-feed sensor triggers mid-batch.

    Carries the pages already captured successfully so the caller can
    still process them instead of discarding the whole batch.
    """

    def __init__(self, sheet_index: int, pages_captured: list, reason: str | None = None):
        super().__init__(f"Multi-feed detected at sheet {sheet_index}")
        self.sheet_index = sheet_index
        self.pages_captured = pages_captured
        # The driver's own words when a read failed (a jam, an I/O error),
        # rather than the multi-feed sensor tripping. None for the sensor.
        self.reason = reason
