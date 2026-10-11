import logging
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image
from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)

# Where Homebrew, a pip --user install and the usual installers put ocrmypdf,
# tesseract and ghostscript. The menu bar app's LaunchAgent starts with a
# minimal PATH (the venv, /usr/bin, /bin), so before this every scan made
# through it silently came out without a text layer: ocrmypdf wasn't found
# and the pages were kept image-only (seen on real scans, 2026-10-11).
TOOL_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin"))


def extend_tool_path(environ=os.environ) -> None:
    """Appends TOOL_DIRS missing from PATH. Child processes (the scanix500
    CLI, ocrmypdf, tesseract) inherit it."""
    parts = [p for p in environ.get("PATH", "").split(os.pathsep) if p]
    environ["PATH"] = os.pathsep.join(parts + [d for d in TOOL_DIRS if d not in parts])


extend_tool_path()

# ocrmypdf/Tesseract default to English-only OCR if no language is given —
# confirmed on real hardware that this silently produced degraded accuracy
# on German documents (umlauts, ß, word segmentation) even after the "deu"
# language pack was installed, since nothing ever told ocrmypdf to use it.
OCR_LANGUAGE = "deu+eng"


def _save_image_only_pdf(image: Image.Image, path: Path) -> None:
    # Without an explicit resolution Pillow writes 72-DPI page geometry, which
    # turns a 300-DPI A4 scan into a hugely oversized PDF page.
    dpi = image.info.get("dpi", (200, 200))[0]
    # 1-bit and gray pages stay as they are; converting to RGB would triple
    # the size of a black & white scan for nothing.
    page = image if image.mode in ("1", "L") else image.convert("RGB")
    page.save(path, format="PDF", resolution=dpi)


def _merge_pdfs(page_paths: list[Path], output_path: Path) -> None:
    writer = PdfWriter()
    for page_path in page_paths:
        writer.append(str(page_path))
    with open(output_path, "wb") as f:
        writer.write(f)


def _ocr_single_page(input_path: Path) -> Path:
    output_path = input_path.with_suffix(".ocr.pdf")
    try:
        subprocess.run(
            # --deskew straightens slight tilts while adding the text layer.
            # It replaces the driver's swdeskew (see capture.py), and leaves a
            # large tilt alone instead of guessing: checked on a real scan,
            # 3 degrees came out straight, 20 degrees unchanged.
            ["ocrmypdf", "-l", OCR_LANGUAGE, "--deskew", str(input_path), str(output_path)],
            check=True,
            capture_output=True,
        )
        return output_path
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        logger.warning("OCR failed for %s; including as image-only", input_path)
        return input_path


class OcrFailed(Exception):
    """No searchable PDF could be made (ocrmypdf missing, the input unreadable)."""


OCR_WHOLE_FILE_TIMEOUT = 30 * 60
OCR_PAGE_TIMEOUT = 10 * 60


def _ocrmypdf(input_path: Path, output_path: Path, languages: str, timeout: float) -> None:
    subprocess.run(["ocrmypdf", "-l", languages, "--deskew", str(input_path), str(output_path)],
                   check=True, capture_output=True, timeout=timeout)


def ocr_pdf(input_path: Path, output_path: Path, languages: list[str]) -> None:
    """Makes a searchable copy of an image-only PDF. Tries the whole file
    first; if that fails, each page on its own, keeping a page OCR can't
    read without text. Raises OcrFailed when nothing could be made."""
    codes = "+".join(dict.fromkeys(languages)) or OCR_LANGUAGE
    try:
        _ocrmypdf(input_path, output_path, codes, OCR_WHOLE_FILE_TIMEOUT)
        return
    except FileNotFoundError as e:
        raise OcrFailed("ocrmypdf isn't installed, so the scan couldn't be made searchable.") from e
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        logger.warning("OCR of %s failed as a whole; trying page by page", input_path)
    try:
        reader = PdfReader(str(input_path))
    except Exception as e:  # noqa: BLE001
        raise OcrFailed(f"The scan's PDF couldn't be read: {e}") from e
    any_done = False
    with tempfile.TemporaryDirectory() as tmp:
        page_paths = []
        for i, page in enumerate(reader.pages):
            single = Path(tmp) / f"page_{i}.pdf"
            writer = PdfWriter()
            writer.add_page(page)
            with open(single, "wb") as f:
                writer.write(f)
            done = Path(tmp) / f"page_{i}.ocr.pdf"
            try:
                _ocrmypdf(single, done, codes, OCR_PAGE_TIMEOUT)
                page_paths.append(done)
            except FileNotFoundError as e:
                raise OcrFailed("ocrmypdf isn't installed, so the scan couldn't be made searchable.") from e
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
                page_paths.append(single)  # this page stays without text
                continue
            any_done = True
        if not any_done:
            raise OcrFailed("No page could be made searchable — is the OCR language installed?")
        _merge_pdfs(page_paths, output_path)


_installed_languages: list[str] | None = None
_installed_checked = False


def installed_ocr_languages() -> list[str] | None:
    """Tesseract's installed language codes (`tesseract --list-langs`), looked
    up once. None when that can't be asked, meaning: don't filter."""
    global _installed_languages, _installed_checked
    if not _installed_checked:
        _installed_checked = True
        try:
            out = subprocess.run(["tesseract", "--list-langs"], check=True, capture_output=True,
                                 text=True, timeout=30).stdout
            langs = [ln.strip() for ln in out.splitlines()[1:] if ln.strip()]
            _installed_languages = langs or None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            _installed_languages = None
    return _installed_languages


def build_pdf(images: list[Image.Image], output_path: Path, *, ocr: bool = True) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        page_paths = []
        for i, image in enumerate(images):
            page_path = tmp_dir_path / f"page_{i}.pdf"
            _save_image_only_pdf(image, page_path)
            page_paths.append(_ocr_single_page(page_path) if ocr else page_path)
        _merge_pdfs(page_paths, output_path)
