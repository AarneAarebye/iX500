"""Version 2 of the Dossiary scan bridge protocol (dossiary-scan-helper's
PROTOCOL.md): the iX500's scanner entry, scan request validation, and
turning a scan result into a response. No HTTP and no rumps/AppKit here."""

from __future__ import annotations

import base64
from pathlib import Path

from pypdf import PdfReader

from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult
from scanix500.scanners import IX500, IX500_ID, get_scanner  # noqa: F401 - IX500 is re-exported

EMPTY_FEEDER_MESSAGE = "No paper in the feeder."
ALL_BLANK_MESSAGE = "Every page was blank, so no PDF was made."
NOT_CONNECTED_MESSAGE = get_scanner(IX500_ID).not_connected_message


class ProtocolError(Exception):
    """A request the protocol refuses: HTTP status plus a message for a person."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def error_body(message: str) -> dict:
    # "error" repeats the message for Dossiary 1.43, which reads that key.
    return {"ok": False, "partial": False, "message": message, "error": message}


def validate(body: object, scanners: list[dict]) -> tuple[dict, dict]:
    """PROTOCOL.md "Request validation" steps 1-5. Returns the scanner and
    the settings with every default filled in."""
    if not isinstance(body, dict):
        raise ProtocolError(400, "The scan request must be a JSON object.")
    scanner_id = body.get("scanner")
    if not isinstance(scanner_id, str):
        raise ProtocolError(400, "The scan request needs a scanner id.")
    scanner = next((s for s in scanners if s["id"] == scanner_id), None)
    if scanner is None:
        raise ProtocolError(404, f"No scanner with id “{scanner_id}”.")

    for key in ("source", "colorMode", "format"):
        if key in body and not isinstance(body[key], str):
            raise ProtocolError(400, f"“{key}” must be text.")
    if "resolution" in body and (not isinstance(body["resolution"], int) or isinstance(body["resolution"], bool)):
        raise ProtocolError(400, "“resolution” must be a whole number.")
    if "duplex" in body and not isinstance(body["duplex"], bool):
        raise ProtocolError(400, "“duplex” must be true or false.")
    extras = body.get("extras", {})
    if not isinstance(extras, dict) or not all(isinstance(v, bool) for v in extras.values()):
        raise ProtocolError(400, "“extras” must map names to true or false.")

    if "ocr" in body and not isinstance(body["ocr"], bool):
        raise ProtocolError(400, "“ocr” must be true or false.")
    languages = body.get("ocrLanguages", [])
    if not isinstance(languages, list) or not all(isinstance(lang, str) for lang in languages):
        raise ProtocolError(400, "“ocrLanguages” must be a list of language codes.")

    name = scanner["name"]
    source = body.get("source", scanner["sources"][0])
    if source not in scanner["sources"]:
        raise ProtocolError(422, f"{name} has no {source} source.")
    color_mode = body.get("colorMode", scanner["colorModes"][0])
    if color_mode not in scanner["colorModes"]:
        raise ProtocolError(422, f"{name} can't scan in {color_mode}.")
    resolution = body.get("resolution", scanner["resolutions"][0])
    if resolution not in scanner["resolutions"]:
        raise ProtocolError(422, f"{name} can't scan at {resolution} dpi.")
    fmt = body.get("format", "pdf")
    if fmt != "pdf":
        raise ProtocolError(422, f"Format “{fmt}” isn't supported; use pdf.")
    duplex = body.get("duplex", False)
    if duplex and not (scanner["duplex"] and source == "feeder"):
        raise ProtocolError(422, f"{name} can't scan both sides from the {source}.")
    for extra, on in extras.items():
        if on and extra not in scanner["extras"]:
            raise ProtocolError(422, f"{name} doesn't offer “{extra}”.")

    ocr = body.get("ocr", False)
    offered = (scanner.get("ocr") or {}).get("languages", [])
    if ocr and not offered:
        raise ProtocolError(422, f"{name} can't make a searchable PDF.")
    if ocr:
        for lang in languages:
            if lang not in offered:
                raise ProtocolError(422, f"{name} has no OCR language “{lang}”.")

    return scanner, {"source": source, "duplex": duplex, "colorMode": color_mode,
                     "resolution": resolution, "format": fmt, "extras": dict(extras),
                     "ocr": ocr, "ocrLanguages": list(dict.fromkeys(languages))}


def pages_message(count: int) -> str:
    return "1 page scanned." if count == 1 else f"{count} pages scanned."


def profile_for(settings: dict, destination: str, scanner: str = IX500_ID) -> Profile:
    """The one-off Profile a version 2 request runs. OCR stays on, as for
    every scanix500 scan, unless a background job does it. No extras means no blank filtering at all."""
    extras = settings["extras"]
    split = extras.get("splitOnBlank", False)
    skip_blank = extras.get("skipBlankPages", False)
    return Profile(
        name="Dossiary Bridge Scan",
        destination=destination,
        skip_blank_filter=not (split or skip_blank),
        skip_ocr=bool(settings.get("ocr", False)),
        split_on_blank=split,
        duplex=settings["duplex"],
        color_mode=settings["colorMode"],
        resolution=settings["resolution"],
        keep_blank_pages=split and not skip_blank,
        scanner=scanner,
    )


def _file_entry(path: str) -> dict | None:
    try:
        data = Path(path).read_bytes()
        pages = len(PdfReader(path).pages)
    except Exception:  # noqa: BLE001 - an unreadable file is left out, not fatal
        return None
    return {"name": Path(path).name, "pages": pages, "data": base64.b64encode(data).decode("ascii")}


def _first_part(message: str) -> str:
    # runner.py appends " — partial scan saved to <path>"; the path means
    # nothing to Dossiary.
    return message.split(" — ")[0].strip()


def response_from_result(result: ScanResult, scanner_id: str = IX500_ID) -> tuple[int, dict]:
    known = get_scanner(scanner_id) or get_scanner(IX500_ID)
    files = [entry for entry in (_file_entry(p) for p in result.output_paths) if entry]
    if result.ok:
        if files:
            total = sum(f["pages"] for f in files)
            return 200, {"ok": True, "partial": False, "message": pages_message(total), "files": files}
        if result.message == "No pages scanned.":
            return 500, error_body(EMPTY_FEEDER_MESSAGE)
        if result.message.startswith("All pages were blank"):
            return 500, error_body(ALL_BLANK_MESSAGE)
        return 500, error_body("The scan finished, but its PDF couldn't be read.")
    if result.partial and files:
        return 200, {"ok": False, "partial": True, "message": _first_part(result.message), "files": files}
    prefix = "Scanner not found: "
    if result.message.startswith("Scanner not found"):
        if known.not_found_text in result.message:
            return 503, error_body(known.not_connected_message)
        detail = _first_part(result.message[len(prefix):] if result.message.startswith(prefix) else result.message)
        return 503, error_body(f"The {known.name} can't be used right now: {detail}")
    if result.message.startswith("Scanner setup failed"):
        return 500, error_body(_first_part(result.message))
    return 500, error_body(_first_part(result.message) or "The scan failed.")
