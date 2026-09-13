import argparse
import sys
from pathlib import Path

from scanix500.blank_filter import filter_pages
from scanix500.capture import PySaneDevice, capture_pages
from scanix500.errors import MultiFeedError, NoPagesScannedError, ScannerNotFoundError
from scanix500.pdf_builder import build_pdf
from scanix500.router import compute_output_paths


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="scanix500")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--skip-blank-filter", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--split-on-blank", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        device = PySaneDevice()
        pages = capture_pages(device)
    except ScannerNotFoundError as e:
        print(f"Scanner not found: {e}", file=sys.stderr)
        return 1
    except NoPagesScannedError:
        print("No pages scanned.")
        return 0
    except MultiFeedError as e:
        print(
            f"Multi-feed detected at sheet {e.sheet_index}; "
            f"processing {len(e.pages_captured)} pages captured before the jam.",
            file=sys.stderr,
        )
        # Intentional fall-through (not an early return): the pages captured
        # before the jam must still be filtered, built and routed by the shared
        # code below. Only the exit code differs from the success path.
        pages = e.pages_captured
        exit_code = 1
    else:
        exit_code = 0

    if args.skip_blank_filter:
        partitions = [
            [img for pair in pages for img in (pair.front, pair.back)]
        ]
    else:
        partitions = filter_pages(pages, split_on_blank=args.split_on_blank)

    if not partitions:
        print("All pages were blank; no PDF written.")
        return exit_code

    output_paths = compute_output_paths(args.destination, len(partitions))
    try:
        args.destination.mkdir(parents=True, exist_ok=True)
        for images, path in zip(partitions, output_paths):
            build_pdf(images, path, ocr=not args.skip_ocr)
            print(path)
    except OSError as e:
        print(
            f"Failed to write output to {args.destination}: {e}",
            file=sys.stderr,
        )
        return 1

    return exit_code
