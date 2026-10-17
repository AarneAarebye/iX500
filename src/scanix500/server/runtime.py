"""The running server: one scan at a time, the Scan button watched only
while a pairing request waits, and the HTTPS listener."""

from __future__ import annotations

import logging
import socket
import ssl
import threading
from collections.abc import Callable
from http.server import ThreadingHTTPServer

from scanix500.menubar.bridge import RemoteOptions, ScanBusyError, make_handler_class
from scanix500.menubar.button_watcher import button_pressed, resolve_device_name
from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult, run_scan
from scanix500.server.button_pairing import ButtonPairing
from scanix500.server.config import ServerConfig

logger = logging.getLogger(__name__)


class ServerScanTrigger:
    """ScanTrigger for the server: runs the scan in the request's own
    thread; a second request while one runs gets ScanBusyError (409)."""

    def __init__(self, run: Callable[[Profile], ScanResult] = run_scan):
        self._run = run
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def trigger(self, profile: Profile) -> ScanResult:
        if not self._lock.acquire(blocking=False):
            raise ScanBusyError()
        try:
            try:
                return self._run(profile)
            except Exception as e:  # noqa: BLE001 - always answer
                return ScanResult(ok=False, partial=False, message=str(e), output_paths=[])
        finally:
            self._lock.release()


class ButtonWatcher:
    """Polls the iX500's Scan button only while a button-pairing request
    is waiting and no scan runs, so the scanner isn't opened every 1.5 s
    for nothing."""

    def __init__(self, button_pairing: ButtonPairing, trigger: ServerScanTrigger,
                 resolve: Callable[[], str | None] = resolve_device_name,
                 pressed: Callable[[str], bool] = button_pressed, interval: float = 1.5):
        self._pairing, self._trigger = button_pairing, trigger
        self._resolve, self._pressed, self._interval = resolve, pressed, interval
        self._device: str | None = None
        self._stop = threading.Event()

    def poll_once(self) -> None:
        if not self._pairing.has_pending() or self._trigger.busy:
            return
        if self._device is None:
            self._device = self._resolve()
            if self._device is None:
                return
        if self._pressed(self._device):
            self._pairing.press()

    def start(self) -> None:
        def loop():
            while not self._stop.wait(self._interval):
                try:
                    self.poll_once()
                except Exception:  # noqa: BLE001 - never stop watching over one hiccup
                    self._device = None
        threading.Thread(target=loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


class _DualStackServer(ThreadingHTTPServer):
    """Listens on IPv6 and IPv4 at once (helpers may reach the Pi either way)."""
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()

    def handle_error(self, request, client_address) -> None:
        # A plain-HTTP request, a client giving up mid-handshake or a dropped
        # connection: one line in the journal, not a traceback per attempt.
        import sys
        error = sys.exc_info()[1]
        logger.warning("Connection from %s failed: %s", client_address[0], error)


def start_remote_server(config: ServerConfig, backend, pairing, remote: RemoteOptions,
                        context: ssl.SSLContext) -> ThreadingHTTPServer:
    handler = make_handler_class(lambda: str(config.scans_path), None, pairing, backend, remote=remote)
    handler.timeout = 60  # a client that stalls mid-request can't hold a thread forever
    httpd = _DualStackServer(("::", config.port), handler)
    # The TLS handshake runs in each request's own thread, not in accept().
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
