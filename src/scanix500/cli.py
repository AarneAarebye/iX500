import argparse
import sys
from pathlib import Path

from scanix500 import __version__
from scanix500.bw import to_black_and_white
from scanix500.blank_filter import filter_pages
from scanix500.capture import PagePair, PySaneDevice, PySaneFlatbed, capture_pages
from scanix500.errors import MultiFeedError, NoPagesScannedError, ScannerNotFoundError, ScannerSetupError
from scanix500.pdf_builder import build_pdf
from scanix500.router import compute_output_paths
from scanix500.scanners import CANOSCAN_8800F_ID, IX500_ID, KNOWN_SCANNERS
from scanix500.snap import snap_to_standard_size
from scanix500.trim import trim_background


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="scanix500")
    parser.add_argument("--version", action="version", version=f"scanix500 {__version__}")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--skip-blank-filter", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--split-on-blank", action="store_true")
    parser.add_argument("--simplex", action="store_true", help="scan the front of each sheet only")
    parser.add_argument("--mode", choices=("color", "gray", "bw"), default="color")
    parser.add_argument("--resolution", type=int, default=300)
    parser.add_argument("--keep-blank-pages", action="store_true",
                        help="keep blank pages (with --split-on-blank, a blank separator sheet is still dropped)")
    parser.add_argument("--scanner", choices=[s.id for s in KNOWN_SCANNERS], default=IX500_ID,
                        help="which scanner to use (the CanoScan 8800F scans one flatbed page)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    flatbed = args.scanner == CANOSCAN_8800F_ID

    try:
        if flatbed:
            device = PySaneFlatbed(color_mode=args.mode, resolution=args.resolution)
            pages = capture_pages(device, duplex=False)
        else:
            device = PySaneDevice(duplex=not args.simplex, color_mode=args.mode, resolution=args.resolution)
            pages = capture_pages(device, duplex=not args.simplex)
    except ScannerNotFoundError as e:
        print(f"Scanner not found: {e}", file=sys.stderr)
        return 1
    except ScannerSetupError as e:
        print(f"Scanner setup failed: {e}", file=sys.stderr)
        return 1
    except NoPagesScannedError:
        print("No pages scanned.")
        return 0
    except MultiFeedError as e:
        if e.reason and not e.pages_captured:
            # Stopped before any sheet came through: nothing to keep, so a
            # plain failure rather than a partial scan of zero pages.
            print(f"Scan failed: the scanner stopped at sheet 1 ({e.reason}).", file=sys.stderr)
            return 1
        if e.reason:
            # A failed read: the driver's own words say what happened.
            # sheet_index counts the sheets finished before it.
            kept = len(e.pages_captured)
            print(
                f"Scanner stopped at sheet {e.sheet_index + 1} ({e.reason}); "
                f"kept the {kept} sheet{'' if kept == 1 else 's'} before it.",
                file=sys.stderr,
            )
        else:
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
    except Exception as e:
        print(f"Scan failed: {e}", file=sys.stderr)
        return 1
    else:
        exit_code = 0

    if flatbed:
        # One page straight from the glass: the lid is white, so there's no
        # background to trim, the A4 scan area is the page, and one page
        # needs no blank check.
        partitions = [[pair.front for pair in pages]]
    else:
        # Trim the scanner's background from the sides first, so an A5 sheet
        # comes out at A5 width and the blank check only sees the paper; then a
        # page within a few mm of a standard size gets exactly that size.
        def fit(image):
            return snap_to_standard_size(trim_background(image))

        pages = [PagePair(fit(p.front), None if p.back is None else fit(p.back)) for p in pages]

        if args.skip_blank_filter:
            partitions = [
                [img for pair in pages for img in (pair.front, pair.back) if img is not None]
            ]
        else:
            partitions = filter_pages(
                pages, split_on_blank=args.split_on_blank, drop_blank_pages=not args.keep_blank_pages
            )

    if not partitions:
        print("All pages were blank; no PDF written.")
        return exit_code

    if args.mode == "bw":
        partitions = [[to_black_and_white(img) for img in images] for images in partitions]

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
