from scanix500.menubar.runner import ScanResult, parse_scan_output


def test_parse_scan_output_success_with_paths():
    result = parse_scan_output(
        returncode=0,
        stdout="/tmp/scans/scan_2026-09-13_143012.pdf\n",
        stderr="",
    )

    assert result == ScanResult(
        ok=True,
        partial=False,
        message="Scan complete",
        output_paths=["/tmp/scans/scan_2026-09-13_143012.pdf"],
    )
