"""`scanix500-server`: the iX500 as a network scanner on a Raspberry Pi.

  init                  make the server's certificate (once; the package does this)
  run [--fake-scanners] the service itself (systemd starts it)
  pair                  print a 6-digit pairing code, valid 5 minutes
  clients               list paired computers; `clients revoke NAME` removes one
  status                version, port, fingerprint, scanner, paired computers
  avahi-service         print the mDNS announcement (the package writes it)
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from xml.sax.saxutils import escape

from scanix500 import __version__
from scanix500.menubar.bridge import RealBackend, RemoteOptions
from scanix500.menubar.button_watcher import resolve_device_name
from scanix500.menubar.fakes import FakeBackend, FakePairing
from scanix500.menubar.ocr_jobs import OcrJobQueue
from scanix500.menubar.pairing import SERVER_CODE_LIFETIME_SECONDS, SharedWindowPairingStore
from scanix500.server.button_pairing import ButtonPairing
from scanix500.server.config import ServerConfig, load_config
from scanix500.server.runtime import ButtonWatcher, ServerScanTrigger, start_remote_server
from scanix500.server.tls import certificate_sha256, display_fingerprint, generate_certificate, server_context


def _store(cfg: ServerConfig) -> SharedWindowPairingStore:
    return SharedWindowPairingStore(cfg.pairing_path, cfg.window_path)


def _fingerprint_line(cfg: ServerConfig) -> str:
    return f"Fingerprint: {display_fingerprint(certificate_sha256(cfg.cert_path))}"


def cmd_init(cfg: ServerConfig) -> int:
    made = generate_certificate(cfg.tls_dir, cfg.name)
    print(("Created the server's certificate." if made else "The server's certificate already exists."))
    print(_fingerprint_line(cfg))
    return 0


def cmd_run(cfg: ServerConfig, fake: bool) -> int:
    if not (cfg.cert_path.exists() and cfg.key_path.exists()):
        print(f"No certificate in {cfg.tls_dir}. Create one with: sudo scanix500-server init", file=sys.stderr)
        return 1
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg.scans_path.mkdir(parents=True, exist_ok=True)
    if fake:
        pairing = FakePairing()
        backend = FakeBackend()
    else:
        pairing = _store(cfg)
        queue = OcrJobQueue(cfg.ocr_jobs_path)
        queue.start()
        trigger = ServerScanTrigger()
        backend = RealBackend(lambda: str(cfg.scans_path), trigger, ocr_jobs=queue, keep_outputs=False)
    buttons = ButtonPairing(pairing.issue_token)
    fingerprint = certificate_sha256(cfg.cert_path)
    remote = RemoteOptions(fingerprint=fingerprint, name=cfg.name, button_pairing=buttons)
    httpd = start_remote_server(cfg, backend, pairing, remote, server_context(cfg.cert_path, cfg.key_path))
    if not fake:
        ButtonWatcher(buttons, trigger).start()
    logging.info("scanix500-server %s listening on port %d as %s (%s)", __version__, cfg.port, cfg.name,
                 display_fingerprint(fingerprint))
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    try:
        stopped.wait()
    except KeyboardInterrupt:
        pass
    httpd.shutdown()
    return 0


def cmd_pair(cfg: ServerConfig) -> int:
    code = _store(cfg).open_window()
    minutes = SERVER_CODE_LIFETIME_SECONDS // 60
    print(f"Pairing code: {code}  (valid {minutes} minutes)")
    if cfg.cert_path.exists():
        print(_fingerprint_line(cfg))
        print("Check that Dossiary Scan Helper shows the same fingerprint before you enter the code.")
    return 0


def cmd_clients(cfg: ServerConfig, revoke: str | None) -> int:
    store = _store(cfg)
    if revoke is not None:
        if store.revoke(revoke):
            print(f"Removed “{revoke}”. It has to pair again to scan.")
            return 0
        print(f"No paired computer is called “{revoke}”.", file=sys.stderr)
        return 1
    rows = store.clients()
    if not rows:
        print("No computer is paired yet.")
    for row in rows:
        print(f"{row['name']}  paired {row['paired_at'] or '?'}  last used {row['last_used'] or 'never'}")
    return 0


def cmd_status(cfg: ServerConfig) -> int:
    print(f"scanix500-server {__version__}")
    print(f"Name: {cfg.name}")
    print(f"Port: {cfg.port}")
    print(_fingerprint_line(cfg) if cfg.cert_path.exists() else "Certificate: missing (run: sudo scanix500-server init)")
    device = resolve_device_name()
    print(f"Scanner: {device}" if device else "Scanner: not found")
    print(f"Paired computers: {_store(cfg).paired_count()}")
    return 0


def cmd_avahi_service(cfg: ServerConfig) -> int:
    print(f"""<?xml version="1.0" standalone="no"?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<!-- Written by scanix500-server's package from /etc/scanix500/server.conf. -->
<service-group>
  <name>{escape(cfg.name)}</name>
  <service>
    <type>_dossiary-scan._tcp</type>
    <port>{cfg.port}</port>
    <txt-record>proto=2</txt-record>
  </service>
</service-group>""")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scanix500-server", description="The iX500 as a network scanner.")
    parser.add_argument("--version", action="version", version=f"scanix500-server {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="make the server's certificate")
    run = sub.add_parser("run", help="run the server")
    run.add_argument("--fake-scanners", action="store_true", help="serve the protocol's fake scanners (testing)")
    sub.add_parser("pair", help="print a pairing code")
    clients = sub.add_parser("clients", help="list paired computers")
    clients_sub = clients.add_subparsers(dest="action")
    revoke = clients_sub.add_parser("revoke", help="remove a paired computer")
    revoke.add_argument("name")
    sub.add_parser("status", help="show the server's state")
    sub.add_parser("avahi-service", help="print the Avahi service file (the package writes it)")
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.command == "init":
        return cmd_init(cfg)
    if args.command == "run":
        return cmd_run(cfg, args.fake_scanners)
    if args.command == "avahi-service":
        return cmd_avahi_service(cfg)
    if args.command == "pair":
        return cmd_pair(cfg)
    if args.command == "clients":
        return cmd_clients(cfg, args.name if args.action == "revoke" else None)
    return cmd_status(cfg)


if __name__ == "__main__":
    sys.exit(main())
