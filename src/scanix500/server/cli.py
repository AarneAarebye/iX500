"""`scanix500-server`: the iX500 as a network scanner on a Raspberry Pi.

  init                  make the server's certificate (once; the package does this)
  run [--fake-scanners] the service itself (systemd starts it)
  pair                  print a 6-digit pairing code, valid 5 minutes
  clients               list paired computers; `clients revoke NAME` removes one
  button-scans          list the finished button scans; `discard-open` drops the open collection
  status                version, port, fingerprint, scanner, paired computers
  avahi-service         print the mDNS announcement (the package writes it)
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from xml.sax.saxutils import escape

from scanix500 import __version__
from scanix500.i18n import choose_language, set_language, t
from scanix500.collection import ScanCollection
from scanix500.menubar.bridge import RealBackend, RemoteOptions
from scanix500.menubar.button_watcher import resolve_device_name
from scanix500.menubar.fakes import FakeBackend, FakePairing
from scanix500.menubar.ocr_jobs import OcrJobQueue
from scanix500.menubar.pairing import SERVER_CODE_LIFETIME_SECONDS, SharedWindowPairingStore
from scanix500.server.button_pairing import ButtonPairing
from scanix500.server.button_scans import ButtonScanStore
from scanix500.server.buttons import ButtonCollector
from scanix500.server.config import ServerConfig, load_config
from scanix500.server.runtime import ButtonWatcher, ServerScanTrigger, start_remote_server
from scanix500.server.tls import certificate_sha256, display_fingerprint, generate_certificate, server_context


def _store(cfg: ServerConfig) -> SharedWindowPairingStore:
    return SharedWindowPairingStore(cfg.pairing_path, cfg.window_path)


def _fingerprint_line(cfg: ServerConfig) -> str:
    return t("Fingerprint: {fingerprint}", fingerprint=display_fingerprint(certificate_sha256(cfg.cert_path)))


def cmd_init(cfg: ServerConfig) -> int:
    made = generate_certificate(cfg.tls_dir, cfg.name)
    print((t("Created the server's certificate.") if made else t("The server's certificate already exists.")))
    print(_fingerprint_line(cfg))
    return 0


def cmd_run(cfg: ServerConfig, fake: bool) -> int:
    if not (cfg.cert_path.exists() and cfg.key_path.exists()):
        print(t("No certificate in {folder}. Create one with: sudo scanix500-server init", folder=cfg.tls_dir), file=sys.stderr)
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
    button_store = collector = None
    if not fake:
        button_store = ButtonScanStore(cfg.button_scans_path)
        button_store.start()
        collector = ButtonCollector(ScanCollection(cfg.collection_path), button_store, trigger, cfg)
    httpd = start_remote_server(cfg, backend, pairing, remote, server_context(cfg.cert_path, cfg.key_path),
                                button_scans=button_store)
    if not fake:
        ButtonWatcher(buttons, trigger).start()
        collector.start()
    logging.info("scanix500-server %s listening on port %d as %s (%s)", __version__, cfg.port, cfg.name,
                 display_fingerprint(fingerprint))
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    try:
        stopped.wait()
    except KeyboardInterrupt:
        pass
    httpd.shutdown()
    if collector is not None:
        collector.stop()
        button_store.stop()
    return 0


def cmd_pair(cfg: ServerConfig) -> int:
    code = _store(cfg).open_window()
    minutes = SERVER_CODE_LIFETIME_SECONDS // 60
    print(t("Pairing code: {code}  (valid {minutes} minutes)", code=code, minutes=minutes))
    if cfg.cert_path.exists():
        print(_fingerprint_line(cfg))
        print(t("Check that Dossiary Scan Helper shows the same fingerprint before you enter the code."))
    return 0


def cmd_clients(cfg: ServerConfig, revoke: str | None) -> int:
    store = _store(cfg)
    if revoke is not None:
        if store.revoke(revoke):
            print(t("Removed “{name}”. It has to pair again to scan.", name=revoke))
            return 0
        print(t("No paired computer is called “{name}”.", name=revoke), file=sys.stderr)
        return 1
    rows = store.clients()
    if not rows:
        print(t("No computer is paired yet."))
    for row in rows:
        print(t("{name}  paired {paired}  last used {used}", name=row['name'], paired=row['paired_at'] or '?', used=row['last_used'] or t("never")))
    return 0


def _paired_count(cfg: ServerConfig) -> int:
    # Opened first on purpose: the pairing store treats an unreadable file as
    # empty, which would show "0" to a user who just may not read it.
    try:
        with open(cfg.pairing_path, "rb"):
            pass
    except FileNotFoundError:
        return 0
    return _store(cfg).paired_count()


def cmd_status(cfg: ServerConfig) -> int:
    print(f"scanix500-server {__version__}")
    print(t("Name: {name}", name=cfg.name))
    print(t("Port: {port}", port=cfg.port))
    # The state folder is readable only by the service's user and root (it
    # holds scans and pairings), so without sudo some lines can't be shown.
    try:
        print(_fingerprint_line(cfg) if cfg.cert_path.exists() else t("Certificate: missing (run: sudo scanix500-server init)"))
    except PermissionError:
        print(t("Fingerprint: (run with sudo to see it)"))
    device = resolve_device_name()
    print(t("Scanner: {device}", device=device) if device else t("Scanner: not found"))
    try:
        print(t("Paired computers: {count}", count=_paired_count(cfg)))
    except PermissionError:
        print(t("Paired computers: (run with sudo to see them)"))
    for line in _button_status_lines(cfg):
        print(line)
    return 0


def _read_collection(cfg: ServerConfig) -> ScanCollection | None:
    """The open collection, read-only (None when the folder doesn't exist)."""
    if not cfg.collection_path.is_dir():
        return None
    return ScanCollection(cfg.collection_path)


def _button_status_lines(cfg: ServerConfig) -> list[str]:
    try:
        collection = _read_collection(cfg)
        if collection is not None and collection.page_count:
            last = collection.last_activity or time.time()
            left = max(0, int(last + cfg.collect_timeout_minutes * 60 - time.time()))
            lines = [t("Button scans: collecting {count} pages (closes in {minutes} min)",
                       count=collection.page_count, minutes=(left + 59) // 60)]
        else:
            lines = [t("Button scans: nothing collected")]
        waiting = len(ButtonScanStore(cfg.button_scans_path).list()) if cfg.button_scans_path.is_dir() else 0
        lines.append(t("Waiting for pickup: {count}", count=waiting))
        return lines
    except PermissionError:
        return [t("Button scans: (run with sudo to see them)")]


def cmd_button_scans(cfg: ServerConfig, action: str, yes: bool) -> int:
    try:
        if action == "list":
            if not cfg.button_scans_path.is_dir():
                print(t("No button scans are waiting."))
                return 0
            rows = ButtonScanStore(cfg.button_scans_path).list()
            if not rows:
                print(t("No button scans are waiting."))
            for row in rows:
                print(t("{name}  {pages} pages  {created}", name=row["name"], pages=row["pages"], created=row["created"]))
            return 0
        collection = _read_collection(cfg)
        if collection is None or not collection.page_count:
            print(t("Button scans: nothing collected"))
            return 0
        if not yes:
            answer = input(t("Throw away {count} collected pages? [y/N] ", count=collection.page_count))
            if answer.strip().lower() not in ("y", "yes"):
                return 1
        collection.discard()
        print(t("Discarded the open collection."))
        return 0
    except PermissionError:
        print(t("Button scans: (run with sudo to see them)"), file=sys.stderr)
        return 1


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
    set_language(choose_language(os.environ))
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
    bs = sub.add_parser("button-scans", help="list finished button scans, or discard the open collection "
                                             "(stop the service first: sudo systemctl stop scanix500-server)")
    bs.add_argument("action", choices=["list", "discard-open"], nargs="?", default="list")
    bs.add_argument("--yes", action="store_true", help="don't ask")
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
    if args.command == "button-scans":
        return cmd_button_scans(cfg, args.action, args.yes)
    return cmd_status(cfg)


if __name__ == "__main__":
    sys.exit(main())
