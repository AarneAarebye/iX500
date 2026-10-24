"""The protocol's fake scanner mode (PROTOCOL.md "Fake scanner mode"), so
dossiary-scan-helper's conformance suite can test this bridge without an
iX500. Ported from that repo's reference helper (MIT). Testing only."""

from __future__ import annotations

import base64
import io
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from PIL import Image

from scanix500.menubar.bridge import ScanBusyError
from scanix500.menubar.pairing import PairingError
from scanix500.menubar.protocol_v2 import EMPTY_FEEDER_MESSAGE, ProtocolError, error_body, images_message, pages_message

FAKE_PAIRING_CODE = "000000"


def _entry(id: str, name: str, sources: list, duplex: bool = False, extras: tuple = ()) -> dict:
    return {"id": id, "name": name, "sources": sources, "duplex": duplex,
            "colorModes": ["color", "gray", "bw"], "resolutions": [150, 300, 600], "extras": list(extras)}


FAKE_SCANNERS = [
    _entry("fake:flatbed", "Fake Flatbed", ["flatbed"]),
    _entry("fake:feeder", "Fake Feeder", ["feeder", "flatbed"], duplex=True),
    _entry("fake:splitter", "Fake Splitter", ["feeder"], extras=("splitOnBlank", "skipBlankPages")),
    _entry("fake:jam", "Fake Jamming Feeder", ["feeder"]),
    _entry("fake:slow", "Fake Slow Scanner", ["flatbed"]),
    _entry("fake:offline", "Fake Offline Scanner", ["flatbed"]),
    _entry("fake:empty", "Fake Empty Feeder", ["feeder"]),
    {**_entry("fake:ocr", "Fake OCR Scanner", ["flatbed"]), "ocr": {"languages": ["deu", "eng"]}},
    {**_entry("fake:photo", "Fake Photo Scanner", ["flatbed"]), "resolutions": [150, 300, 600, 1200],
     "formats": ["pdf", "jpeg", "png"], "areas": ["a4", "10x15"]},
]


@dataclass(frozen=True)
class _Behaviour:
    sheets: tuple = ()
    delay: float = 0.0
    offline: bool = False
    jam_after: int | None = None
    empty: bool = False


_BEHAVIOUR = {
    "fake:feeder": _Behaviour(sheets=("Sheet 1", "Sheet 2", "Sheet 3")),
    "fake:splitter": _Behaviour(sheets=("Doc A page 1", "Doc A page 2", "", "Doc B page 1")),
    "fake:jam": _Behaviour(sheets=("Sheet 1", "Sheet 2", "Sheet 3"), jam_after=2),
    "fake:slow": _Behaviour(delay=2.0),
    "fake:offline": _Behaviour(offline=True),
    "fake:empty": _Behaviour(empty=True),
}


def simulate(scanner: dict, settings: dict) -> tuple[list[list[str]], bool, str]:
    """Runs a fake scan: (documents as lists of page texts, partial, message)."""
    b = _BEHAVIOUR.get(scanner["id"], _Behaviour())
    if b.offline:
        raise ProtocolError(503, f"{scanner['name']} isn't reachable. Check that it's switched on and connected.")
    if b.delay:
        time.sleep(b.delay)
    if b.empty:
        raise ProtocolError(500, EMPTY_FEEDER_MESSAGE)
    if settings.get("format", "pdf") != "pdf":
        return [["Flatbed page 1"]], False, images_message(1)

    if settings["source"] == "flatbed":
        pages = ["Flatbed page 1"]
    else:
        sheets = b.sheets if b.jam_after is None else b.sheets[: b.jam_after]
        pages = []
        for sheet in sheets:
            pages.append(sheet)
            if settings["duplex"]:
                pages.append(f"{sheet} back" if sheet else "")

    extras = settings["extras"]
    if extras.get("splitOnBlank"):
        documents, current = [], []
        for page in pages:
            if page == "":
                if current:
                    documents.append(current)
                current = []
            else:
                current.append(page)
        if current:
            documents.append(current)
    else:
        if extras.get("skipBlankPages"):
            pages = [p for p in pages if p != ""]
        documents = [pages]

    if b.jam_after is not None and settings["source"] == "feeder":
        return documents, True, f"Paper jam after {b.jam_after} sheets. The pages before it were kept."
    return documents, False, pages_message(sum(len(d) for d in documents))


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(page_texts: list[str]) -> bytes:
    """A PDF with one A4 page per text ("" gives a blank page)."""
    if not page_texts:
        raise ValueError("a PDF needs at least one page")
    count = len(page_texts)
    # Object numbers: 1 catalog, 2 page tree, 3 font, then a page and its content per text.
    page_numbers = [4 + 2 * i for i in range(count)]
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(f"{n} 0 R" for n in page_numbers), count)).encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_number, text in zip(page_numbers, page_texts):
        bodies.append((
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_number + 1} 0 R >>"
        ).encode())
        stream = f"BT /F1 36 Tf 72 720 Td ({_escape(text)}) Tj ET".encode("latin-1")
        bodies.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def make_image(fmt: str) -> bytes:
    """A tiny valid 8 x 8 JPEG or PNG (the fake photo scanner's scan)."""
    out = io.BytesIO()
    Image.new("L", (8, 8), 200).save(out, "JPEG" if fmt == "jpeg" else "PNG")
    return out.getvalue()


class FakeOcrJobs:
    """In-memory background OCR jobs: serial, each ready `delay` seconds after
    the previous one, deleted on fetch, dropped after a week."""

    def __init__(self, delay=2.0, clock=time.monotonic, retention=7 * 24 * 3600):
        self._delay = delay
        self._clock = clock
        self._retention = retention
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def submit(self, pdf: bytes, name: str, pages: int) -> str:
        with self._lock:
            now = self._clock()
            last = max((j["ready_at"] for j in self._jobs.values()), default=now)
            job_id = secrets.token_urlsafe(24)
            self._jobs[job_id] = {"ready_at": max(now, last) + self._delay, "created_at": now,
                                  "name": name, "pages": pages, "pdf": pdf}
            return job_id

    def fetch(self, job_id: str) -> tuple[int, dict]:
        with self._lock:
            now = self._clock()
            for jid in [j for j, job in self._jobs.items() if now - job["created_at"] > self._retention]:
                del self._jobs[jid]
            job = self._jobs.get(job_id)
            if job is None:
                return 404, {"ok": False, "partial": False,
                             "message": "This OCR job is unknown, already fetched, or expired."}
            if now >= job["ready_at"]:
                del self._jobs[job_id]
                return 200, {"ok": True, "files": [{"name": job["name"], "pages": job["pages"],
                                                    "data": base64.b64encode(job["pdf"]).decode("ascii")}]}
            ahead = sum(1 for j in self._jobs.values() if now < j["ready_at"] < job["ready_at"])
            return 202, {"ok": True, "state": "running" if ahead == 0 else "queued", "position": ahead}


class FakeBackend:
    def __init__(self):
        self._lock = threading.Lock()
        self._ocr = FakeOcrJobs()

    def scanners(self) -> list[dict]:
        return [dict(s) for s in FAKE_SCANNERS]

    def scan(self, scanner: dict, settings: dict) -> tuple[int, dict]:
        if not self._lock.acquire(blocking=False):
            raise ScanBusyError()
        try:
            documents, partial, message = simulate(scanner, settings)
        except ProtocolError as e:
            return e.status, error_body(e.message)
        finally:
            self._lock.release()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fmt = settings.get("format", "pdf")
        if fmt != "pdf":
            extension = "jpg" if fmt == "jpeg" else "png"
            entry = {"name": f"scan_{stamp}.{extension}", "pages": 1,
                     "data": base64.b64encode(make_image(fmt)).decode("ascii")}
            return 200, {"ok": True, "partial": False, "message": message, "files": [entry]}
        files = []
        for i, pages in enumerate(documents, start=1):
            name = f"scan_{stamp}.pdf" if len(documents) == 1 else f"scan_{stamp}_{i}.pdf"
            entry = {"name": name, "pages": len(pages)}
            if settings.get("ocr"):
                entry["data"] = base64.b64encode(make_pdf([""] * len(pages))).decode("ascii")
                entry["ocrJob"] = self._ocr.submit(make_pdf(pages), name, len(pages))
            else:
                entry["data"] = base64.b64encode(make_pdf(pages)).decode("ascii")
            files.append(entry)
        return 200, {"ok": not partial, "partial": partial, "message": message, "files": files}

    def ocr_job(self, job_id: str) -> tuple[int, dict]:
        return self._ocr.fetch(job_id)


class FakePairing:
    """Fake scanner mode's pairing: the code is always 000000, any number
    of times, never expiring (PROTOCOL.md "Pairing"). Tokens live in memory."""

    def __init__(self):
        self._tokens: set[str] = set()
        self._lock = threading.Lock()

    def issue_token(self, client: str) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._tokens.add(token)
        return token

    def pair(self, code: str, client: str = "") -> str:
        if code != FAKE_PAIRING_CODE:
            raise PairingError("That code isn't right. Choose “Pair a browser…” in the helper and enter the new code.")
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._tokens.add(token)
        return token

    def is_valid(self, token: str | None) -> bool:
        with self._lock:
            return bool(token) and token in self._tokens
