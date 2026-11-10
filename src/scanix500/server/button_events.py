"""A press of an 8800F button, announced at once: a counter for /health and
an optional administrator-configured command (speaker, LED)."""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)

COMMAND_TIMEOUT_SECONDS = 5


class ButtonEvents:
    def __init__(self, command: Sequence[str] | None = None, run: Callable = subprocess.run):
        self._command = list(command) if command else None
        self._run = run
        self._lock = threading.Lock()
        self._seq = 0
        self._button: str | None = None

    def record(self, button: str) -> None:
        with self._lock:
            self._seq += 1
            self._button = button
        if self._command:
            threading.Thread(target=self._feedback, args=(button,), daemon=True).start()

    def snapshot(self) -> dict:
        with self._lock:
            if self._button is None:
                return {"seq": self._seq}
            return {"seq": self._seq, "button": self._button}

    def _feedback(self, button: str) -> None:
        try:
            self._run([*self._command, button], timeout=COMMAND_TIMEOUT_SECONDS, check=False,
                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:  # noqa: BLE001 - feedback must never matter to a scan
            logger.warning("Button feedback command failed: %s", type(exc).__name__)
