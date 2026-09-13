class ScannerNotFoundError(Exception):
    """Raised when the SANE fujitsu backend can't find the iX500."""


class NoPagesScannedError(Exception):
    """Raised when the ADF was empty and no pages were captured."""


class MultiFeedError(Exception):
    """Raised when the scanner's multi-feed sensor triggers mid-batch.

    Carries the pages already captured successfully so the caller can
    still process them instead of discarding the whole batch.
    """

    def __init__(self, sheet_index: int, pages_captured: list):
        super().__init__(f"Multi-feed detected at sheet {sheet_index}")
        self.sheet_index = sheet_index
        self.pages_captured = pages_captured
