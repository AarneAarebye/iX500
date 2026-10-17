# scanix500 on a Raspberry Pi: a network scan server

Date: 2026-10-17
Repos: iX500 (server mode, Debian package); dossiary-scan-helper (network
scanners in the macOS helper, protocol addition)

## Goal

The iX500 hangs off a Raspberry Pi instead of a Mac, and every computer in
the house scans with it from Dossiary, the way a network printer is shared:

- the scanner needn't be plugged into the computer that scans;
- several computers (Macs now, Windows PCs once the Windows helper exists)
  share it, one scan at a time.

Dossiary itself doesn't change: it keeps talking only to the scan helper on
its own computer (`localhost`), so its security model stays as it is.

## Architecture

```
Dossiary ──localhost──▶ Dossiary Scan Helper ──LAN, HTTPS──▶ scanix500-server (Pi) ──USB──▶ iX500
                        lists local + network scanners        scans, splits, OCRs
```

1. **`scanix500-server`** (iX500 repo): the existing bridge without the
   menu bar app, as a Linux service. Same protocol version 2 (scanners,
   scan, background OCR) with the iX500 pipeline unchanged (blank pages,
   split on blank, multi-feed handling, deskew, trim, ocrmypdf), served
   over HTTPS on the local network and announced via mDNS.
2. **Network scanners in Dossiary Scan Helper** (helper repo, macOS): a
   `RemoteBackend` implementing `ScannerBackend` that finds servers, pairs
   once, lists their scanners next to local ones and forwards scan and
   background-OCR requests. The Windows helper, when built, gets the same
   piece; it isn't designed here.
3. **Debian package `scanix500-server`** (iX500 repo): the way the server is
   installed and updated on the Pi.

Built in that order; each part has its own implementation plan.

## 1. Server mode

- New console script `scanix500-server` (`src/scanix500/server/`), no
  rumps/AppKit import anywhere in its import graph. Subcommands:
  `run` (the service), `pair` (print a pairing code), `clients`
  (list), `clients revoke <name>`, `status` (device found, port,
  certificate fingerprint, paired clients).
- Reuses `bridge.py` (the HTTP handler and protocol v2), `pairing.py`,
  `ocr_jobs.py`, the capture/pipeline modules and `button_watcher.py`. The
  scan "trigger" the menu bar app supplies today is replaced by a
  server-side runner with the same busy guard: one scan at a time; a second
  request gets protocol v2's existing busy answer.
- **Listening**: HTTPS on all interfaces, port **8767** by default
  (`/etc/scanix500/server.conf`), so it can't be mistaken for the
  loopback-only HTTP bridge on 8765. TLS via Python's `ssl` module with the
  certificate and key from `/var/lib/scanix500/tls/`; the server refuses to
  start without them.
- **Requests from web pages are refused**: any request carrying an `Origin`
  header gets 403. Only helpers talk to the server, so no website on any
  computer in the house can probe or use it.
- **Without a token** only `GET /health` and the pairing endpoints answer.
  `/health` adds `remote: true` and `fingerprint` (SHA-256 of the
  certificate, shown as 8 groups of 4 hex digits).
- State under `/var/lib/scanix500/` (pairings, OCR jobs, TLS), settings in
  `/etc/scanix500/server.conf`: port and the name announced on the network
  (default: the Pi's hostname). OCR languages come with each scan request,
  as today.
- Scans are returned in the response and deleted on the Pi; background OCR
  results wait for their client with the OCR job queue's existing
  retention.

## 2. Pairing and security

- **Certificate pinning**: the helper stores the server's certificate
  fingerprint when pairing and checks it on every connection. A changed
  certificate is refused with a message naming the server, never trusted
  silently.
- **Button pairing (normal)**: the helper's "Add network scanner…" lists
  discovered servers; choosing one sends `POST /pair/button {client}` →
  `202 {request}`; the helper polls `GET /pair/button/<request>` while
  telling the person "Press the Scan button on the iX500 within 2 minutes".
  The server's button watcher, on a press while exactly one request is
  pending, answers that request `200 {token}`. Two pending requests at once
  → both refused (`409`), so a second party can't slip in; no press within
  2 minutes → `408`.
- **Code pairing (without access to the scanner)**: `scanix500-server pair`
  (over SSH) prints a 6-digit code valid for 5 minutes plus the fingerprint;
  the helper's existing `POST /pair {code, client}` flow takes the code and
  shows the fingerprint to compare.
- Pairing attempts are limited: a code window closes after 5 wrong codes
  (a new one needs `scanix500-server pair` again), and button-pairing
  requests are limited to 10 a minute (429).
- Tokens are stored hashed with the client name and last use;
  `clients revoke` removes one. The helper keeps its token and pinned
  fingerprint in the macOS Keychain.
- **The Scan button only pairs** on the server. Scanning by button into a
  chosen library ("scan to my Inbox") is a possible later feature, not part
  of this.
- PROTOCOL.md (helper repo) gains a "Remote servers" section: the button
  pairing endpoints, `remote`/`fingerprint` in `/health`, the Origin rule,
  and HTTPS. Existing loopback helpers are unaffected.

## 3. Helper: network scanners (macOS)

- Discovery with Bonjour (`NWBrowser`, `_dossiary-scan._tcp`); a server can
  also be added by host name for networks without mDNS.
- `RemoteBackend: ScannerBackend` per paired server: `listScanners()` maps
  the server's scanners to ids `remote:<server>:<id>` and names
  "<name> (<server name>)"; `scan()` forwards settings and returns the
  server's documents; background OCR jobs are forwarded and their ids
  mapped, so Dossiary polls the helper as for a local scanner.
- An unreachable server's scanners are left out of the list, not shown as
  errors. A 401 from the server marks it "needs pairing again" in the menu.
- Menu: "Add network scanner…", and per paired server its name, state and
  "Remove".

## 4. Debian package

**Target**: Raspberry Pi OS based on Debian 13 ("trixie"), 64-bit or
32-bit. Debian 12 ships pypdf 3.4, older than scanix500's pypdf >= 4.0
requirement, and isn't supported. Recommended hardware: Raspberry Pi 4
(2 GB or more) or Pi 5; a Pi 3 works but OCR is slow; a Zero 2 W (512 MB)
is too small for ocrmypdf.

**Package** `scanix500-server_<version>_all.deb` (pure Python, one package
for every Pi):

- **Depends**: `python3 (>= 3.11)`, `python3-sane`, `python3-pil`,
  `python3-pypdf (>= 4)`, `sane-utils`, `ocrmypdf`, `tesseract-ocr`,
  `tesseract-ocr-deu`, `tesseract-ocr-eng`, `avahi-daemon`, `openssl`.
  **Suggests**: the other `tesseract-ocr-*` languages. All from Raspberry Pi
  OS's own repositories, so no virtualenv and no compiling on the Pi.
- **Files**:
  - `/usr/lib/scanix500/scanix500/` (the Python package),
    `/usr/bin/scanix500-server` (a wrapper setting `PYTHONPATH`);
  - `/lib/systemd/system/scanix500-server.service`: `User=scanix500`,
    `SupplementaryGroups=scanner` (SANE's USB access), `StateDirectory=
    scanix500`, `Restart=on-failure`, and hardening (`NoNewPrivileges`,
    `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`);
  - `/etc/avahi/services/scanix500.service`: `_dossiary-scan._tcp` on the
    configured port, TXT `proto=2`;
  - `/etc/scanix500/server.conf`, a conffile, so a person's changes survive
    upgrades.
- **postinst**: creates the system user `scanix500` (in `scanner`); creates
  the certificate if missing (`openssl`, EC P-256, CN = hostname, valid 10
  years); `systemctl enable --now scanix500-server`; prints the fingerprint
  and how to pair.
- **Upgrade** (`sudo apt install ./newer.deb`): the service restarts;
  certificate, pairings and settings in `/var/lib` and `/etc` are kept, so
  no computer has to pair again.
- **prerm** stops the service; **postrm purge** removes the user,
  `/var/lib/scanix500` and `/etc/scanix500`.
- **Built** by `packaging/debian/build-deb.sh` with `dpkg-deb` from a staged
  tree (no debhelper needed), version taken from `pyproject.toml`, checked
  with `lintian`. A GitHub Actions job builds it on each `v*` tag and
  attaches it to the release.
- **Install guide** (`docs/raspberry-pi.md`, linked from the README):
  flash Raspberry Pi OS Lite with Raspberry Pi Imager (setting host name,
  Wi-Fi and SSH there), plug in the iX500, download the `.deb` from the
  release, `sudo apt install ./scanix500-server_<version>_all.deb`, check
  `scanix500-server status`, then pair from each computer.

## Error handling

- iX500 not connected: `/scanners` lists nothing and `status` says so; the
  server keeps running and picks the scanner up when it's plugged in.
- Missing or unreadable certificate: the service fails to start with a
  journal message saying how to recreate it (`dpkg-reconfigure
  scanix500-server`).
- Busy scanner, multi-feed, partial scans: protocol v2's existing answers,
  passed through by the helper unchanged.

## Testing

- iX500 repo: unit tests for the Origin rule, button pairing (one pending,
  two pending, timeout), code pairing rate limit, token revocation, and the
  server's startup without a certificate; the existing conformance suite
  run against `scanix500-server run --fake-scanners` over HTTPS.
- Package: in CI, install the built `.deb` in a Debian 13 container, check
  files, user, `systemd-analyze verify` of the unit, and that
  `scanix500-server run --fake-scanners` answers `/health` over HTTPS;
  `lintian` clean.
- Helper repo: tests for `RemoteBackend` against a fake server (listing,
  scan forwarding, OCR job mapping, pin mismatch refused, 401 handling).
- By hand on a real Pi with the iX500: install, pair by button and by code,
  duplex scan with blank-page splitting from two Macs, a scan while the
  other is scanning gets "busy", upgrade keeps pairings, purge removes
  everything.

## Out of scope

- Scanning by pressing the button into a chosen library.
- A ready-made SD card image (the package is its foundation).
- The Windows helper and installers for the computers.
- Dossiary talking to the Pi directly.
