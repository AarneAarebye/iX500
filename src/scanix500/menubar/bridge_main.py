"""`scanix500-bridge --fake-scanners`: the HTTP bridge on its own, with the
protocol's fake scanners and pairing code 000000, for running
dossiary-scan-helper's conformance suite. Testing only -- the real bridge
runs inside scanix500-menubar."""

from __future__ import annotations

import argparse
import threading

from scanix500.menubar.bridge import DEFAULT_PORT, start_bridge_server
from scanix500.menubar.fakes import FakeBackend, FakePairing


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="scanix500-bridge")
    parser.add_argument("--fake-scanners", action="store_true", help="serve the protocol's fake scanners")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if not args.fake_scanners:
        parser.error("only --fake-scanners is supported; the real bridge runs inside scanix500-menubar")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    server = start_bridge_server(lambda: "", None, FakePairing(), port=args.port, backend=FakeBackend())
    print(f"scanix500 bridge with fake scanners on http://127.0.0.1:{args.port}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0
