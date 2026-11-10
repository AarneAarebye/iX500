# Button press feedback (scanix500 0.10.1, Dossiary Scan Helper 0.6.1)

Date: 2026-11-10. Scope: `scanix500-server` (Pi) and the macOS Dossiary Scan Helper.

## Problem

After a press of a CanoScan 8800F button, the scan needs about 8 s until the first image byte (measured on node-000: press seen within 0.65 s, first byte 7.96 s after the scan command, whole 300 dpi colour scan 11.4 s). The wait is the scanner's lamp warm-up and calibration; a faster reader would save 1 to 2 s at most. What is missing is an immediate sign that the press was registered.

## Decisions (agreed 2026-11-10)

1. Feedback on the Mac (a sound from the helper) **and** a hook on the Pi for hardware feedback (speaker, LED), both now.
2. The scanner's own reaction (lamp, carriage) is not changed. It starts with the scan command, which already goes out right after detection.

## Design

### Pi (`scanix500-server` 0.10.1)

- `ButtonCollector` (`server/buttons.py`) keeps a counter `seq` (starts at 0 on each server start, memory only) and the name of the last pressed button: `color`, `bw`, `custom` or `finish`. When a press is read, the counter is bumped and the name set **before** the scan or the finish starts.
- `/health` gets `"buttonEvent": {"seq": <int>, "button": "<name>"}` when button scans are enabled (next to `"buttonScans": true`); before the first press `seq` is 0 and `button` is absent.
- Optional setting `button_feedback_command` in the server config (default empty). When set, the collector runs it on each press without a shell (the string is split like a shell command line), with the button name as the last argument, in a background thread, killed after 5 s. Failures and timeouts are logged and never delay or fail the scan. The command is administrator configuration, not network input.
- `scanix500-server status` shows the setting. README and `docs/raspberry-pi.md` get a short section with an example.

### Mac helper (0.6.1)

- `ButtonScanPickup` polls servers that report `"buttonScans": true` once per second (was every 5 s). The poll that reads `/health` already exists; the request count per server goes from 0.2 to 1 per second on the local network. Only the idle interval changes; the download and delete logic is unchanged.
- It remembers the last `seq` per server. The first value after start-up or after a reconnect is only stored. A larger `seq`, or a smaller one (the server restarted), with a `button` name plays a sound. Several presses between two polls play one sound.
- Sound: one short system sound for COLOR, B&W and CUSTOM, a different one for FINISH (fixed choices, no setting for which sound).
- Menu item "Sound on button press" (check mark, on by default), stored in the helper's settings. Texts in all seven languages in `Localizable.xcstrings`.
- `PROTOCOL.md` documents `buttonEvent` as an optional field of `/health`.

## Errors

- A server without `buttonEvent` (0.10.0): no sound, polling still at 1 s. No error.
- A sound that cannot play is ignored.
- Malformed `buttonEvent` (wrong types): ignored.

## Tests

- Pi: a press bumps `seq` and sets `button` before the scan call; the feedback command is called with the button name, a failing or hanging command does not delay the scan, an empty setting runs nothing; `/health` shows the field; config parsing of the new key.
- Helper: first reading does not play; increase plays once; decrease (restart) plays; several presses between polls play once; FINISH plays the other sound; the switch off plays nothing; missing or malformed field plays nothing; the idle poll interval is 1 s.
- Hardware (manual): COLOR, B&W, CUSTOM and FINISH each make the Mac sound within about 1 s of the press, the scan runs as before; with `button_feedback_command` set to a command that writes a line to a file, the line appears per press.

## Out of scope

Changing the scanner's lamp or warm-up behaviour, push channels (long polling, server-sent events), a choice of sounds, notifications on the Mac, the Mac menu bar app (it reads the buttons itself).

## Release

scanix500 0.10.1 (tag `v0.10.1`, CI builds the `.deb`, update node-000 and node-001) and Dossiary Scan Helper 0.6.1 (tag `macos-v0.6.1`, signed, notarized, installed). README and AGENTS.md of both repos get the new setting and field.
