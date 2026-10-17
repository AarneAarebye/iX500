# scanix500 on a Raspberry Pi

Share the ScanSnap iX500 over the network: it hangs off a Raspberry Pi,
and every computer with Dossiary Scan Helper scans with it.

## What you need

- A Raspberry Pi 4 (2 GB or more) or Pi 5. A Pi 3 works, but turning
  scans into searchable PDFs is slow; a Zero 2 W is too small.
- Raspberry Pi OS **Lite**, based on Debian 12 ("bookworm") or Debian 13
  ("trixie"), 64-bit recommended.
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
