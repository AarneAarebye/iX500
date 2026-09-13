from datetime import datetime
from pathlib import Path

from scanix500.router import compute_output_paths


def test_compute_output_paths_single_file_has_timestamp_name():
    fixed_now = datetime(2026, 9, 13, 14, 30, 12)

    paths = compute_output_paths(Path("/dest"), count=1, now=fixed_now)

    assert paths == [Path("/dest/scan_2026-09-13_143012.pdf")]


def test_compute_output_paths_multiple_files_are_sequentially_numbered():
    fixed_now = datetime(2026, 9, 13, 14, 30, 12)

    paths = compute_output_paths(Path("/dest"), count=3, now=fixed_now)

    assert paths == [
        Path("/dest/scan_2026-09-13_143012_1.pdf"),
        Path("/dest/scan_2026-09-13_143012_2.pdf"),
        Path("/dest/scan_2026-09-13_143012_3.pdf"),
    ]
