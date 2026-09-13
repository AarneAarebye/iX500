from scanix500.errors import MultiFeedError, NoPagesScannedError, ScannerNotFoundError


def test_multi_feed_error_carries_sheet_index_and_pages():
    err = MultiFeedError(sheet_index=3, pages_captured=["a", "b"])
    assert err.sheet_index == 3
    assert err.pages_captured == ["a", "b"]
    assert "3" in str(err)


def test_no_pages_scanned_and_scanner_not_found_are_exceptions():
    assert issubclass(NoPagesScannedError, Exception)
    assert issubclass(ScannerNotFoundError, Exception)
