"""Browser pairing for the HTTP bridge.

The bridge's origin check alone can't tell Dossiary apart from any other
web page: a page opened from file:// sends `Origin: null`, but so does any
website's sandboxed iframe. So each browser pairs once: "Pair a Browser…"
in the menu shows a 6-digit code (valid 2 minutes, 5 attempts), Dossiary
sends it to POST /pair and gets back a random token, and every scan request
must then carry `Authorization: Bearer <token>`. Only SHA-256 hashes of the
tokens are stored, so the file on disk can't be used to scan. Same scheme
as dossiary-scan-helper's PROTOCOL.md ("Pairing").

No rumps/AppKit dependency here, like bridge.py -- fully unit-testable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

CODE_LIFETIME_SECONDS = 120
CODE_ATTEMPTS = 5

_BEARER = re.compile(r"Bearer ([A-Za-z0-9_-]+)")


class PairingError(Exception):
    """A pairing attempt failed: no open window, wrong/expired code, or
    no attempts left. The message is written for a person."""


def default_pairing_path() -> Path:
    return Path.home() / "Library" / "Application Support" / "scanix500" / "paired_browsers.json"


def bearer_token(header: str | None) -> str | None:
    """The token from an `Authorization: Bearer <token>` header, or None."""
    if not header:
        return None
    match = _BEARER.fullmatch(header)
    return match.group(1) if match else None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PairingStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.monotonic):
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._code: str | None = None
        self._expires_at = 0.0
        self._attempts_left = 0
        self._hashes = self._load()

    def _load(self) -> set[str]:
        try:
            data = json.loads(self._path.read_text())
            return {h for h in data.get("token_hashes", []) if isinstance(h, str)}
        except (OSError, ValueError, AttributeError):
            return set()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), prefix=self._path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"token_hashes": sorted(self._hashes)}, f)
            os.replace(tmp_name, self._path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def open_window(self) -> str:
        """Starts a pairing window and returns its 6-digit code. Opening a
        new window replaces any earlier code."""
        with self._lock:
            self._code = f"{secrets.randbelow(1_000_000):06d}"
            self._expires_at = self._clock() + CODE_LIFETIME_SECONDS
            self._attempts_left = CODE_ATTEMPTS
            return self._code

    def pair(self, code: str) -> str:
        """Exchanges the open window's code for a new token. A code works
        once; a wrong code uses up an attempt, and the last one closes the
        window."""
        with self._lock:
            if self._code is None or self._clock() > self._expires_at or self._attempts_left <= 0:
                self._code = None
                raise PairingError(
                    "No pairing code is active. Choose “Pair a Browser…” in the scanix500 menu and enter the new code."
                )
            if not hmac.compare_digest(code.encode("utf-8"), self._code.encode("utf-8")):
                self._attempts_left -= 1
                if self._attempts_left <= 0:
                    self._code = None
                raise PairingError(
                    "That code isn't right. Check the code shown by “Pair a Browser…” in the scanix500 menu."
                )
            self._code = None
            token = secrets.token_urlsafe(24)
            self._hashes.add(_hash(token))
            self._save()
            return token

    def is_valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            return _hash(token) in self._hashes

    def forget_all(self) -> None:
        with self._lock:
            self._hashes = set()
            self._save()

    def paired_count(self) -> int:
        with self._lock:
            return len(self._hashes)
