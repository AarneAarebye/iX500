# Debian Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `scanix500-server_<version>_all.deb` for Raspberry Pi OS (Debian 13): installs, configures and starts the network scan server, keeps its state across upgrades, and is built and install-tested by GitHub Actions on every `v*` tag — part 3 of `docs/superpowers/specs/2026-10-17-pi-scan-server-design.md`.

**Architecture:** A staged file tree under `packaging/debian/` (control template, maintainer scripts, systemd unit, conffile, wrappers) assembled by `packaging/debian/build-deb.sh` with `dpkg-deb`, no debhelper. The Python package goes to `/usr/lib/scanix500` and runs on Debian's own `python3-sane`/`python3-pil`/`python3-pypdf`. A small `avahi-service` subcommand renders the mDNS announcement from the configured port. CI builds the package, runs lintian, installs it in a `debian:trixie` container and checks the server answers over HTTPS.

**Tech Stack:** POSIX sh, `dpkg-deb`, systemd, Avahi, GitHub Actions.

## Global Constraints

- Package `scanix500-server`, `Architecture: all`, version = `pyproject.toml`'s `version`.
- Depends: `python3 (>= 3.11), python3-sane, python3-pil, python3-pypdf (>= 4), sane-utils, ocrmypdf, tesseract-ocr, tesseract-ocr-deu, tesseract-ocr-eng, avahi-daemon, openssl, adduser`; Suggests: `tesseract-ocr-all`.
- Files: `/usr/lib/scanix500/scanix500/` (no `__pycache__`), `/usr/bin/scanix500-server`, `/usr/bin/scanix500` (the scan CLI the server runs per scan), `/lib/systemd/system/scanix500-server.service`, `/etc/scanix500/server.conf` (conffile), `/etc/avahi/services/scanix500.service` (written by postinst, not shipped).
- Service: `User=scanix500`, `SupplementaryGroups=scanner`, `StateDirectory=scanix500`, `Restart=on-failure`, `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`.
- Maintainer scripts must work without a running systemd (containers): only call `systemctl` when `/run/systemd/system` exists.
- Upgrades keep `/var/lib/scanix500` and `/etc/scanix500`; only `purge` removes them and the user.
- mDNS service type `_dossiary-scan._tcp`, TXT `proto=2`.

---

### Task 1: `avahi-service` subcommand and the package files

**Files:**
- Modify: `src/scanix500/server/cli.py`
- Test: `tests/server/test_cli.py`
- Create: `packaging/debian/control.in`, `packaging/debian/copyright`, `packaging/debian/postinst`, `packaging/debian/prerm`, `packaging/debian/postrm`, `packaging/debian/conffiles`, `packaging/debian/scanix500-server.service`, `packaging/debian/server.conf`, `packaging/debian/scanix500-server.sh`, `packaging/debian/scanix500.sh`, `packaging/debian/build-deb.sh`
- Modify: `.gitignore` (add `dist/`)

**Interfaces:**
- Produces: `scanix500-server avahi-service` prints the Avahi service XML for the configured name and port; `packaging/debian/build-deb.sh` → `dist/scanix500-server_<version>_all.deb`.

- [ ] **Step 1: Failing test** — append to `tests/server/test_cli.py`:

```python
def test_avahi_service_uses_the_configured_port_and_name(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "s.conf"
    conf.write_text("[server]\nport = 9443\nname = Hallway & co\n")
    monkeypatch.setenv("SCANIX500_CONFIG", str(conf))
    monkeypatch.setenv("SCANIX500_STATE_DIR", str(tmp_path / "state"))
    assert main(["avahi-service"]) == 0
    xml = capsys.readouterr().out
    assert "<type>_dossiary-scan._tcp</type>" in xml and "<port>9443</port>" in xml
    assert "Hallway &amp; co" in xml and "<txt-record>proto=2</txt-record>" in xml
```

- [ ] **Step 2: Run it** — `.venv/bin/pytest tests/server/test_cli.py -q` → fails (invalid choice `avahi-service`).

- [ ] **Step 3: Implement** — in `cli.py` add `from xml.sax.saxutils import escape`, the subparser `sub.add_parser("avahi-service", help="print the Avahi service file (the package writes it)")`, the dispatch `if args.command == "avahi-service": return cmd_avahi_service(cfg)`, and:

```python
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
```

and add `  avahi-service         print the mDNS announcement (the package writes it)` to the module docstring.

- [ ] **Step 4: Package files** — create them exactly:

`packaging/debian/control.in`:

```
Package: scanix500-server
Version: @VERSION@
Architecture: all
Maintainer: Aarne Aarebye <25765996+AarneAarebye@users.noreply.github.com>
Installed-Size: @SIZE@
Depends: python3 (>= 3.11), python3-sane, python3-pil, python3-pypdf (>= 4), sane-utils, ocrmypdf, tesseract-ocr, tesseract-ocr-deu, tesseract-ocr-eng, avahi-daemon, openssl, adduser
Suggests: tesseract-ocr-all
Section: graphics
Priority: optional
Homepage: https://github.com/AarneAarebye/iX500
Description: ScanSnap iX500 as a network scanner for Dossiary
 Runs the scanix500 scan pipeline (duplex capture through SANE, blank page
 handling, splitting, ocrmypdf) as a service on a Raspberry Pi, so every
 computer with Dossiary Scan Helper can scan with the iX500 over the local
 network. HTTPS only; computers pair once by pressing the scanner's Scan
 button or with a code from "scanix500-server pair".
```

`packaging/debian/conffiles`:

```
/etc/scanix500/server.conf
```

`packaging/debian/server.conf`:

```ini
# scanix500-server settings. After a change: sudo systemctl restart scanix500-server
# (and sudo dpkg-reconfigure scanix500-server if you changed the port or name,
# so the network announcement follows).
[server]
# port = 8767
# The name computers show for this scanner (default: this Pi's host name).
# name = Hallway scanner
```

`packaging/debian/scanix500-server.service`:

```ini
[Unit]
Description=scanix500 network scan server (ScanSnap iX500)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/scanix500-server run
User=scanix500
SupplementaryGroups=scanner
StateDirectory=scanix500
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
```

`packaging/debian/scanix500-server.sh` (installed as `/usr/bin/scanix500-server`):

```sh
#!/bin/sh
PYTHONPATH=/usr/lib/scanix500${PYTHONPATH:+:$PYTHONPATH}
export PYTHONPATH
exec /usr/bin/python3 -m scanix500.server.cli "$@"
```

`packaging/debian/scanix500.sh` (installed as `/usr/bin/scanix500`):

```sh
#!/bin/sh
PYTHONPATH=/usr/lib/scanix500${PYTHONPATH:+:$PYTHONPATH}
export PYTHONPATH
exec /usr/bin/python3 -c 'import sys; from scanix500.cli import main; sys.exit(main())' "$@"
```

`packaging/debian/postinst`:

```sh
#!/bin/sh
set -e

if [ "$1" = "configure" ]; then
    if ! getent passwd scanix500 >/dev/null; then
        adduser --system --group --home /var/lib/scanix500 --no-create-home scanix500
    fi
    if getent group scanner >/dev/null; then
        adduser scanix500 scanner >/dev/null
    fi
    install -d -o scanix500 -g scanix500 -m 0750 /var/lib/scanix500
    if [ ! -f /var/lib/scanix500/tls/cert.pem ]; then
        runuser -u scanix500 -- /usr/bin/scanix500-server init >/dev/null
    fi
    install -d /etc/avahi/services
    /usr/bin/scanix500-server avahi-service > /etc/avahi/services/scanix500.service

    if [ -d /run/systemd/system ]; then
        systemctl daemon-reload
        systemctl enable scanix500-server.service >/dev/null 2>&1 || true
        if [ -n "$2" ]; then
            systemctl restart scanix500-server.service || true
        else
            systemctl start scanix500-server.service || true
        fi
    fi

    FINGERPRINT=$(runuser -u scanix500 -- /usr/bin/scanix500-server status | sed -n 's/^Fingerprint: //p')
    echo ""
    echo "scanix500-server is installed."
    echo "  Certificate fingerprint: ${FINGERPRINT}"
    echo "  Pair a computer: choose \"Add network scanner...\" in Dossiary Scan Helper and"
    echo "  press the Scan button on the iX500 - or run: sudo scanix500-server pair"
    echo ""
fi

exit 0
```

`packaging/debian/prerm`:

```sh
#!/bin/sh
set -e

if [ -d /run/systemd/system ] && [ "$1" = "remove" ]; then
    systemctl stop scanix500-server.service || true
    systemctl disable scanix500-server.service >/dev/null 2>&1 || true
fi

exit 0
```

`packaging/debian/postrm`:

```sh
#!/bin/sh
set -e

case "$1" in
    remove)
        rm -f /etc/avahi/services/scanix500.service
        ;;
    purge)
        rm -f /etc/avahi/services/scanix500.service
        rm -rf /var/lib/scanix500 /etc/scanix500
        if getent passwd scanix500 >/dev/null; then
            deluser --system scanix500 >/dev/null || true
        fi
        ;;
esac

if [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
fi

exit 0
```

`packaging/debian/build-deb.sh` (executable):

```sh
#!/bin/sh
# Builds dist/scanix500-server_<version>_all.deb from this checkout.
# Needs dpkg-deb (Debian/Ubuntu, or `brew install dpkg` on a Mac).
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -1)
[ -n "$VERSION" ] || { echo "build-deb: no version in pyproject.toml" >&2; exit 1; }
HERE="$ROOT/packaging/debian"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

install -d "$STAGE/DEBIAN" "$STAGE/usr/lib/scanix500" "$STAGE/usr/bin" \
    "$STAGE/lib/systemd/system" "$STAGE/etc/scanix500" \
    "$STAGE/usr/share/doc/scanix500-server"
cp -R "$ROOT/src/scanix500" "$STAGE/usr/lib/scanix500/"
find "$STAGE/usr/lib/scanix500" -name __pycache__ -prune -exec rm -rf {} +
install -m 0755 "$HERE/scanix500-server.sh" "$STAGE/usr/bin/scanix500-server"
install -m 0755 "$HERE/scanix500.sh" "$STAGE/usr/bin/scanix500"
install -m 0644 "$HERE/scanix500-server.service" "$STAGE/lib/systemd/system/"
install -m 0644 "$HERE/server.conf" "$STAGE/etc/scanix500/server.conf"
install -m 0644 "$HERE/copyright" "$STAGE/usr/share/doc/scanix500-server/copyright"
printf 'scanix500-server (%s) unstable; urgency=medium\n\n  * Release notes: https://github.com/AarneAarebye/iX500/releases\n\n -- Aarne Aarebye <25765996+AarneAarebye@users.noreply.github.com>  %s\n' \
    "$VERSION" "$(LC_ALL=C date -R)" | gzip -9n > "$STAGE/usr/share/doc/scanix500-server/changelog.Debian.gz"
for script in postinst prerm postrm; do install -m 0755 "$HERE/$script" "$STAGE/DEBIAN/$script"; done
install -m 0644 "$HERE/conffiles" "$STAGE/DEBIAN/conffiles"
SIZE=$(du -sk "$STAGE" | cut -f1)
sed -e "s/@VERSION@/$VERSION/" -e "s/@SIZE@/$SIZE/" "$HERE/control.in" > "$STAGE/DEBIAN/control"
(cd "$STAGE" && find . -type f ! -path './DEBIAN/*' -exec md5sum {} + | sed 's|  \./|  |' > DEBIAN/md5sums)
find "$STAGE" -type d -exec chmod 0755 {} +
mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/scanix500-server_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null
echo "$OUT"
```

`packaging/debian/copyright`:

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: scanix500
Source: https://github.com/AarneAarebye/iX500

Files: *
Copyright: 2026 Aarne Aarebye
License: MIT
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in
 all copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 SOFTWARE.
```

Add `dist/` to `.gitignore`.

- [ ] **Step 5: Check** — `.venv/bin/pytest -q` (all pass); `sh -n` on every maintainer script and wrapper; `bash -n packaging/debian/build-deb.sh`.

- [ ] **Step 6: Commit**

```bash
git add src/scanix500/server/cli.py tests/server/test_cli.py packaging/debian .gitignore
git commit -m "Debian package: files, maintainer scripts and build script for scanix500-server"
```

---

### Task 2: CI build and install test, install guide

**Files:**
- Create: `.github/workflows/deb.yml`, `docs/raspberry-pi.md`
- Modify: `README.md` (link the guide), `CLAUDE.md` (releasing: the package)

- [ ] **Step 1: Workflow** — `.github/workflows/deb.yml`:

```yaml
name: Debian package

on:
  push:
    tags: ["v*"]
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: sh packaging/debian/build-deb.sh
      - name: Lintian
        run: |
          sudo apt-get update -qq && sudo apt-get install -y -qq lintian
          lintian --fail-on error --suppress-tags no-manual-page,extended-description-line-too-long dist/*.deb
      - uses: actions/upload-artifact@v4
        with:
          name: deb
          path: dist/*.deb

  install-test:
    needs: build
    runs-on: ubuntu-latest
    container: debian:trixie
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: deb
      - name: Install
        run: |
          apt-get update -qq
          DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ./scanix500-server_*_all.deb curl systemd
      - name: Files, user, unit
        run: |
          test -x /usr/bin/scanix500-server && test -x /usr/bin/scanix500
          getent passwd scanix500
          test -f /var/lib/scanix500/tls/cert.pem
          test "$(stat -c %U /var/lib/scanix500/tls/key.pem)" = scanix500
          grep -q "_dossiary-scan._tcp" /etc/avahi/services/scanix500.service
          systemd-analyze verify /lib/systemd/system/scanix500-server.service
          scanix500-server --version
      - name: Server answers over HTTPS
        run: |
          runuser -u scanix500 -- scanix500-server run --fake-scanners &
          sleep 3
          curl -sk https://127.0.0.1:8767/health | grep -q '"remote": true'
      - name: Purge
        run: |
          DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq scanix500-server
          ! getent passwd scanix500
          test ! -e /var/lib/scanix500

  release:
    if: startsWith(github.ref, 'refs/tags/v')
    needs: install-test
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: deb
      - uses: softprops/action-gh-release@v2
        with:
          files: scanix500-server_*_all.deb
```

- [ ] **Step 2: Guide** — `docs/raspberry-pi.md`:

```markdown
# scanix500 on a Raspberry Pi

Share the ScanSnap iX500 over the network: it hangs off a Raspberry Pi,
and every computer with Dossiary Scan Helper scans with it.

## What you need

- A Raspberry Pi 4 (2 GB or more) or Pi 5. A Pi 3 works, but turning
  scans into searchable PDFs is slow; a Zero 2 W is too small.
- Raspberry Pi OS **Lite, 64-bit, based on Debian 13 ("trixie")**. The
  older Debian 12 version is too old for scanix500.
- The iX500 and its USB cable.

## Install

1. Flash Raspberry Pi OS Lite with **Raspberry Pi Imager**. In its settings
   set a host name (e.g. `scanner`), your Wi-Fi (or use a cable), and
   enable SSH.
2. Start the Pi, plug in the iX500 and switch it on.
3. Log in (`ssh scanner.local`) and install the package from the
   [latest release](https://github.com/AarneAarebye/iX500/releases/latest):

       wget https://github.com/AarneAarebye/iX500/releases/download/v<version>/scanix500-server_<version>_all.deb
       sudo apt install ./scanix500-server_<version>_all.deb

   apt installs everything it needs (SANE, ocrmypdf, Tesseract with German
   and English) and starts the service. It prints the certificate
   fingerprint.
4. Check it: `scanix500-server status` shows the scanner, the port (8767)
   and the fingerprint.

## Pair a computer

In Dossiary Scan Helper choose **Add network scanner…**, pick the Pi, and
press the **Scan** button on the iX500 within 2 minutes. Without access to
the scanner, run `sudo scanix500-server pair` on the Pi and type the code
into the helper; compare the fingerprint the helper shows with the one the
Pi printed.

## Day to day

- `scanix500-server clients` lists paired computers;
  `sudo scanix500-server clients revoke "<name>"` removes one.
- Logs: `journalctl -u scanix500-server`.
- Other OCR languages: `sudo apt install tesseract-ocr-fra` (and so on).
- Settings (port, the name computers see): `/etc/scanix500/server.conf`,
  then `sudo dpkg-reconfigure scanix500-server`.

## Update and remove

- Update: download the newer `.deb` and `sudo apt install ./…` it. Pairings,
  certificate and settings are kept.
- Remove: `sudo apt remove scanix500-server`; `sudo apt purge
  scanix500-server` also deletes its settings, pairings and certificate.
```

- [ ] **Step 3: Links** — `README.md`'s "Network scan server (Raspberry Pi)" section already points at `docs/raspberry-pi.md`; change "(part 3 of …)" to "— the package is built by GitHub Actions for every `v*` tag and attached to the release." `CLAUDE.md`'s **Releasing** bullet gains: "Pushing the tag also builds `scanix500-server_<version>_all.deb` (`.github/workflows/deb.yml`: lintian, an install test in `debian:trixie`, then attached to the release); the package files live in `packaging/debian/`."

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/deb.yml docs/raspberry-pi.md README.md CLAUDE.md
git commit -m "Debian package: CI build, install test and release upload; Raspberry Pi guide"
```

- [ ] **Step 5: Verify on GitHub** (needs the user's OK to push): push main, run the workflow by hand (`gh workflow run deb.yml`), watch it (`gh run watch`), and fix anything lintian or the install test reports before tagging a release.
