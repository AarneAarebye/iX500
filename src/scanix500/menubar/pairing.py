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
from datetime import datetime, timedelta, timezone
from pathlib import Path

CODE_LIFETIME_SECONDS = 120
CODE_ATTEMPTS = 5
SERVER_CODE_LIFETIME_SECONDS = 300
LAST_USED_SAVE_INTERVAL = timedelta(hours=1)
UNNAMED_CLIENT = "(paired before names were kept)"

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
    def __init__(self, path: Path, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] | None = None):
        self._path = path
        self._clock = clock
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._code: str | None = None
        self._expires_at = 0.0
        self._attempts_left = 0
        self._hashes, self._clients = self._load()

    def _load(self) -> tuple[set[str], dict[str, dict]]:
        try:
            data = json.loads(self._path.read_text())
            hashes = {h for h in data.get("token_hashes", []) if isinstance(h, str)}
            clients = data.get("clients", {})
            clients = {h: c for h, c in clients.items() if h in hashes and isinstance(c, dict)} if isinstance(clients, dict) else {}
            return hashes, clients
        except (OSError, ValueError, AttributeError):
            return set(), {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), prefix=self._path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"token_hashes": sorted(self._hashes), "clients": self._clients}, f)
            os.replace(tmp_name, self._path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        self._after_save()

    def _after_save(self) -> None:
        """Hook for SharedWindowPairingStore (file ownership, change tracking)."""

    def _issue_locked(self, client: str) -> str:
        self._before_read()  # don't write back another process's revocation as if it never happened
        token = secrets.token_urlsafe(24)
        digest = _hash(token)
        self._hashes.add(digest)
        self._clients[digest] = {"name": (client or "").strip()[:200] or "unnamed",
                                 "paired_at": self._now().isoformat(), "last_used": None}
        self._save()
        return token

    def issue_token(self, client: str) -> str:
        """A new token for `client`, without a code (button pairing)."""
        with self._lock:
            return self._issue_locked(client)

    def open_window(self) -> str:
        """Starts a pairing window and returns its 6-digit code. Opening a
        new window replaces any earlier code."""
        with self._lock:
            self._code = f"{secrets.randbelow(1_000_000):06d}"
            self._expires_at = self._clock() + CODE_LIFETIME_SECONDS
            self._attempts_left = CODE_ATTEMPTS
            return self._code

    def pair(self, code: str, client: str = "") -> str:
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
            return self._issue_locked(client)

    def _before_read(self) -> None:
        """Hook for SharedWindowPairingStore: pick up another process's changes."""

    def is_valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            self._before_read()
            digest = _hash(token)
            if digest not in self._hashes:
                return False
            record = self._clients.get(digest)
            if record is not None:
                now = self._now()
                last = record.get("last_used")
                if last is None or now - datetime.fromisoformat(last) >= LAST_USED_SAVE_INTERVAL:
                    record["last_used"] = now.isoformat()
                    self._save()
            return True

    def clients(self) -> list[dict]:
        with self._lock:
            self._before_read()
            rows = [{"name": self._clients.get(h, {}).get("name", UNNAMED_CLIENT),
                     "paired_at": self._clients.get(h, {}).get("paired_at"),
                     "last_used": self._clients.get(h, {}).get("last_used")} for h in self._hashes]
            return sorted(rows, key=lambda r: r["name"].lower())

    def revoke(self, name: str) -> int:
        """Removes every token paired under `name`; returns how many."""
        with self._lock:
            self._before_read()
            gone = [h for h in self._hashes if self._clients.get(h, {}).get("name", UNNAMED_CLIENT) == name]
            for h in gone:
                self._hashes.discard(h)
                self._clients.pop(h, None)
            if gone:
                self._save()
            return len(gone)

    def forget_all(self) -> None:
        with self._lock:
            self._hashes = set()
            self._clients = {}
            self._save()

    def paired_count(self) -> int:
        with self._lock:
            self._before_read()
            return len(self._hashes)


def _match_dir_owner(path: Path) -> None:
    """When run as root (`sudo scanix500-server pair`), hand a file written
    in the service's state folder to that folder's owner, so the service
    (user scanix500) can still read and replace it."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        st = path.parent.stat()
        try:
            os.chown(path, st.st_uid, st.st_gid)
        except OSError:
            pass


class SharedWindowPairingStore(PairingStore):
    """scanix500-server's pairing: the service and the `scanix500-server
    pair` / `clients` commands are separate processes, so the code window
    lives in a file (only the code's hash, its expiry in wall-clock time and
    the attempts left), and each process re-reads the token file when
    another one changed it."""

    def __init__(self, path: Path, window_path: Path, clock: Callable[[], float] = time.time,
                 now: Callable[[], datetime] | None = None):
        super().__init__(path, clock=clock, now=now)
        self._window_path = window_path
        self._seen_mtime = self._mtime()

    def _mtime(self) -> int | None:
        try:
            return self._path.stat().st_mtime_ns
        except OSError:
            return None

    def _before_read(self) -> None:
        if self._mtime() != self._seen_mtime:
            self._hashes, self._clients = self._load()
            self._seen_mtime = self._mtime()

    def _after_save(self) -> None:
        _match_dir_owner(self._path)
        self._seen_mtime = self._mtime()

    def _write_window(self, window: dict) -> None:
        self._window_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._window_path.parent), prefix=self._window_path.name, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(window, f)
        os.replace(tmp_name, self._window_path)
        _match_dir_owner(self._window_path)

    def open_window(self) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self._lock:
            self._write_window({"code_hash": _hash(code), "expires_at": self._clock() + SERVER_CODE_LIFETIME_SECONDS,
                                "attempts_left": CODE_ATTEMPTS})
        return code

    def pair(self, code: str, client: str = "") -> str:
        no_window = PairingError("No pairing code is active. Run “sudo scanix500-server pair” on the Raspberry Pi "
                                 "and enter the new code.")
        with self._lock:
            self._before_read()
            try:
                window = json.loads(self._window_path.read_text())
            except (OSError, ValueError):
                raise no_window from None
            if not isinstance(window, dict) or self._clock() > window.get("expires_at", 0) \
                    or window.get("attempts_left", 0) <= 0:
                self._window_path.unlink(missing_ok=True)
                raise no_window
            if not hmac.compare_digest(_hash(code), str(window.get("code_hash", ""))):
                window["attempts_left"] = window.get("attempts_left", 0) - 1
                if window["attempts_left"] <= 0:
                    self._window_path.unlink(missing_ok=True)
                else:
                    self._write_window(window)
                raise PairingError("That code isn't right. Check the code “scanix500-server pair” printed.")
            self._window_path.unlink(missing_ok=True)
            return self._issue_locked(client)
