"""Pages of a multi-page scan kept on disk until they are merged into one PDF.

No rumps/AppKit imports here: the Pi server uses this module.
"""
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import PdfReader

from scanix500.pdf_builder import _merge_pdfs

_PAGE_RE = re.compile(r"\Apage-(\d+)\.pdf\Z")


@dataclass(frozen=True)
class CollectionSettings:
    skip_ocr: bool
    ocr_languages: list[str]
    scanner: str
    destination: str = ""


_DEFAULT_SETTINGS = CollectionSettings(
    skip_ocr=False, ocr_languages=["deu", "eng"], scanner="sane:canoscan-8800f"
)


def _is_valid_pdf(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            if f.read(5) != b"%PDF-":
                return False
        return len(PdfReader(str(path)).pages) >= 1
    except Exception:
        return False


class ScanCollection:
    def __init__(self, folder: Path, clock: Callable[[], float] = time.time):
        self._folder = Path(folder)
        self._clock = clock
        self._settings: CollectionSettings | None = None
        self._first_activity: float | None = None
        self._last_activity: float | None = None
        self._folder.mkdir(parents=True, exist_ok=True)
        self._recover()

    # -- properties --------------------------------------------------------
    @property
    def page_count(self) -> int:
        return len(self._pages())

    @property
    def is_open(self) -> bool:
        return self.page_count > 0

    @property
    def settings(self) -> CollectionSettings | None:
        return self._settings if self.is_open else None

    @property
    def last_activity(self) -> float | None:
        return self._last_activity if self.is_open else None

    # -- operations --------------------------------------------------------
    def add_page(self, pdf_path: Path, settings: CollectionSettings) -> int:
        pages = self._pages()
        number = self._next_number(pages)
        now = self._clock()
        if not pages:
            self._settings = settings
            self._first_activity = now
        shutil.move(str(pdf_path), str(self._folder / f"page-{number:03d}.pdf"))
        self._last_activity = now
        self._write_meta()
        return len(pages) + 1

    def expired(self, timeout_seconds: float) -> bool:
        last = self.last_activity
        return last is not None and self._clock() - last >= timeout_seconds

    def finish(self, out_path: Path) -> Path:
        pages = self._pages()
        if not pages:
            raise ValueError("no pages collected")
        out_path = Path(out_path)
        tmp = out_path.with_suffix(".tmp")
        try:
            _merge_pdfs(pages, tmp, title=out_path.stem)
            os.replace(tmp, out_path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        self._clear()
        return out_path

    def discard(self) -> None:
        self._clear()

    def damaged(self) -> list[Path]:
        d = self._folder / "damaged"
        return sorted(d.iterdir()) if d.is_dir() else []

    # -- internals ---------------------------------------------------------
    def _pages(self) -> list[Path]:
        found = []
        for p in self._folder.iterdir():
            m = _PAGE_RE.match(p.name)
            if m:
                found.append((int(m.group(1)), p))
        return [p for _, p in sorted(found)]

    @staticmethod
    def _next_number(pages: list[Path]) -> int:
        if not pages:
            return 1
        return max(int(_PAGE_RE.match(p.name).group(1)) for p in pages) + 1

    def _clear(self) -> None:
        for p in self._pages():
            p.unlink(missing_ok=True)
        (self._folder / "meta.json").unlink(missing_ok=True)
        self._settings = None
        self._first_activity = None
        self._last_activity = None

    def _move_to_damaged(self, path: Path) -> None:
        d = self._folder / "damaged"
        d.mkdir(exist_ok=True)
        target = d / path.name
        n = 1
        while target.exists():
            target = d / f"{path.name}.{n}"
            n += 1
        shutil.move(str(path), str(target))

    def _write_meta(self) -> None:
        meta = {
            "first_activity": self._first_activity,
            "last_activity": self._last_activity,
            "settings": asdict(self._settings),
        }
        fd, tmp = tempfile.mkstemp(dir=self._folder, prefix="meta-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(meta, f)
            os.replace(tmp, self._folder / "meta.json")
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _recover(self) -> None:
        for p in self._pages():
            if not _is_valid_pdf(p):
                self._move_to_damaged(p)
        pages = self._pages()
        meta_path = self._folder / "meta.json"
        meta_ok = False
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                s = meta["settings"]
                self._settings = CollectionSettings(
                    skip_ocr=bool(s["skip_ocr"]),
                    ocr_languages=[str(x) for x in s["ocr_languages"]],
                    scanner=str(s["scanner"]),
                    destination=str(s.get("destination", "")),
                )
                self._first_activity = float(meta["first_activity"])
                self._last_activity = float(meta["last_activity"])
                meta_ok = True
            except Exception:
                self._move_to_damaged(meta_path)
        if not pages:
            if meta_path.exists():
                meta_path.unlink()
            self._settings = self._first_activity = self._last_activity = None
            return
        if not meta_ok:
            mtimes = [p.stat().st_mtime for p in pages]
            self._settings = _DEFAULT_SETTINGS
            self._first_activity = min(mtimes)
            self._last_activity = max(mtimes)
            self._write_meta()
