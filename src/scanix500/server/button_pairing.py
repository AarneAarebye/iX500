"""Pairing a computer by pressing the iX500's Scan button.

A helper asks to pair (POST /pair/button); the request waits up to 2
minutes for the button. Pressing it grants the one pending request a
token; two requests pending at once are both refused, so nobody can slip
a second request in alongside the person standing at the scanner."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable

BUTTON_WINDOW_SECONDS = 120
FORGET_AFTER_SECONDS = 300


class PairingRateLimited(Exception):
    """Too many pairing requests in the last minute."""


class ButtonPairing:
    def __init__(self, issue_token: Callable[[str], str], clock: Callable[[], float] = time.monotonic,
                 limit_per_minute: int = 10):
        self._issue = issue_token
        self._clock = clock
        self._limit = limit_per_minute
        self._lock = threading.Lock()
        self._requests: dict[str, dict] = {}
        self._recent: list[float] = []

    def _tidy_locked(self) -> None:
        now = self._clock()
        for rid, r in list(self._requests.items()):
            if r["state"] == "pending" and now - r["created"] > BUTTON_WINDOW_SECONDS:
                r["state"] = "expired"
            if now - r["created"] > FORGET_AFTER_SECONDS:
                del self._requests[rid]
        self._recent = [t for t in self._recent if now - t < 60]

    def _pending_locked(self) -> list[dict]:
        return [r for r in self._requests.values() if r["state"] == "pending"]

    def request(self, client: str) -> str:
        with self._lock:
            self._tidy_locked()
            if len(self._recent) >= self._limit:
                raise PairingRateLimited()
            now = self._clock()
            self._recent.append(now)
            rid = secrets.token_urlsafe(16)
            self._requests[rid] = {"client": client, "created": now, "state": "pending", "token": None}
            pending = self._pending_locked()
            if len(pending) > 1:
                for r in pending:
                    r["state"] = "conflict"
            return rid

    def status(self, request_id: str) -> tuple[str, str | None]:
        with self._lock:
            self._tidy_locked()
            r = self._requests.get(request_id)
            if r is None:
                return "unknown", None
            if r["state"] == "granted":
                del self._requests[request_id]
                return "granted", r["token"]
            return r["state"], None

    def has_pending(self) -> bool:
        with self._lock:
            self._tidy_locked()
            return bool(self._pending_locked())

    def press(self) -> bool:
        """The Scan button was pressed: grants the one pending request."""
        with self._lock:
            self._tidy_locked()
            pending = self._pending_locked()
            if len(pending) != 1:
                for r in pending:
                    r["state"] = "conflict"
                return False
            r = pending[0]
            r["token"] = self._issue(r["client"])
            r["state"] = "granted"
            return True
