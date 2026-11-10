# CanoScan 8800F on the Pi: button scans collected into one PDF (scanix500 0.10.0)

Date: 2026-11-10. Scope: `scanix500-server` (the Raspberry Pi), the protocol text and conformance tests in `dossiary-scan-helper`, and the macOS helper (`dossiary-scan-helper/macos`). No change to Dossiary's page. The Mac menu bar app follows in `2026-11-10-8800f-multipage-pdf-buttons-design.md`, which shares this spec's collection code. The Pi part is built first: the 8800F is on node-000.

## Problem

`scanix500-server` never reads the 8800F's buttons: its `ButtonWatcher` polls only the iX500's Scan button, and only while a computer waits to pair. And the protocol is request and response (Dossiary or the helper asks, the server answers), so a PDF made by a button press on the Pi would have no one to receive it.

## Decisions (agreed 2026-11-10)

1. **Collecting** as in the Mac spec: COLOR, BLACK & WHITE and CUSTOM each scan a page into the open collection; FINISH or 10 minutes without a press close it into one PDF; nothing is ever discarded; a single page works the same way.
2. **The Pi keeps finished PDFs on a waiting list; the macOS helper fetches them** and saves them to a folder chosen in the helper's menu (default `~/Documents/Scans`, like the Mac scanner's buttons). Dossiary imports from that folder as today.
3. The Pi is built before the Mac app; both use one `ScanCollection`.

## Design

### Shared: `ScanCollection` (new `src/scanix500/collection.py`, no AppKit, no SANE)

Owns a folder (a parameter) with `page-001.pdf`, `page-002.pdf`, … (one-page PDFs scanned without OCR) and `meta.json` (`started`, `last_activity` in wall-clock seconds, page count, and the delivery settings taken at the first press: `skip_ocr`, `ocr_languages`, the scanner id; they do not change if a profile changes meanwhile). Written to disk after every change, atomically. API: `add_page(pdf_path)`, `finish(out_path) -> Path` (merges with `pdf_builder._merge_pdfs`; OCR is not done here), `discard()`, `page_count`, `is_open`, `expired(now, timeout)`, `recover()` (reads an existing folder after a restart; a damaged `meta.json` or a page that is not a PDF is moved to `damaged/` and does not block new pages). `finish()` removes the collection folder only after the merged PDF is in place. The timeout is `collect_timeout_minutes` (default 10, minimum 1).

### Pi server

- **`ButtonCollector`** (new, `src/scanix500/server/buttons.py`) replaces the 8800F half of `ButtonWatcher`. It polls the 8800F's `target` option every 1.5 s (the existing `pixma_button_overrides`, extended so button 7 is a finish action) while no scan runs, opens the SANE device only when the 8800F is on the USB bus (`scanners.py`), and goes through `ServerScanTrigger`'s one-scan-at-a-time lock, so it cannot collide with a scan Dossiary asked for. Pressing 4, 5 or 6 scans one page (colour, black and white, or the configured custom settings) with the existing CLI run (`--scanner sane:canoscan-8800f --skip-ocr`) into a `ScanCollection` under `<state>/collection/`; 7 finishes it. The iX500's pairing button stays in `ButtonWatcher`, unchanged.
- **Custom settings**: the CUSTOM button uses `button_custom_*` keys in `server.conf` (`mode`, `resolution`, `format` is always PDF); defaults gray, 300 dpi. COLOR and BLACK & WHITE use 300 dpi.
- **Finishing**: `ScanCollection.finish()` writes the merged PDF to `<state>/button-scans/<id>.pdf` where `<id>` is a random 22-character URL-safe string, and a `<id>.json` (`created`, `pages`, `ocr: pending|done|failed`). OCR runs once over the whole PDF in `ButtonScanStore`'s own single worker thread (`pdf_builder.ocr_pdf`; not `OcrJobQueue`, whose `fetch` deletes a finished job) and replaces the file when done; the list shows `ocr` so a helper can wait or take it as it is (see below). OCR failure keeps the PDF without text.
- **Waiting list**: files stay until deleted by a client or 30 days after `created`. A disk-space guard refuses to start a page scan when less than 200 MB is free and says so in `status`.
- **`scanix500-server status`** adds "Button scans: collecting 3 pages (closes in 7 min)" and "Waiting for pickup: 2 PDFs". It adds `scanix500-server button-scans list|discard-open` for the person at the Pi.
- The server's systemd unit already has `StateDirectory=scanix500`; the new folders live there.

### Protocol (additions to `PROTOCOL.md`, "Network scan servers"; conformance tests added)

Only on a remote server (HTTPS port 8767), each with the usual `Authorization: Bearer <token>`; no `Origin` allowed (as for every server request).
- `GET /button-scans` → `200 {"scans": [{"id", "name", "pages", "bytes", "created", "ocr"}]}`, oldest first. `ocr` is `"pending"`, `"done"` or `"failed"`.
- `GET /button-scans/<id>` → `200` the PDF (`application/pdf`, `Content-Disposition` with `name`); `404` unknown id. A PDF whose OCR is pending is served as it is, with header `X-Scanix500-Ocr: pending`; the helper may fetch it again later, or take it now.
- `DELETE /button-scans/<id>` → `204`, or `404` if already gone. The helper sends it only after the file is saved.
- `GET /health` adds `"buttonScans": true` so a helper knows the server offers it. Version 1 and 2 requests are unchanged, so older helpers keep working and just never fetch.
- Ids match `\A[A-Za-z0-9_-]{22,}\Z` (as OCR job ids do); anything else is `404`.

### macOS helper

- A new `ButtonScanPickup` in `HelperCore` asks each paired network server whose `/health` says `buttonScans` for its list every 5 s while the server is reachable; a server that is down is skipped silently and tried again later.
- For each listed scan whose `ocr` is `done` or `failed` (or `pending` for more than 5 minutes): download, write to `<folder>/<name>` (`scan_<date>_<time>.pdf`; a name that exists gets ` 2`, ` 3`…), check the PDF starts with `%PDF` and the size matches, then `DELETE`. A scan already saved is recognised by its id in a small `picked-up.json` (kept 30 days), so two Macs or a retry do not write it twice from one Mac.
- Menu: "Folder for button scans…" (a folder chooser, remembered; default `~/Documents/Scans`, created if missing) and a notification "N pages scanned" with the file name when a PDF arrives. A missing or unwritable folder keeps the PDFs on the Pi and shows a menu line "Button scans can't be saved: <reason>". All new texts in the helper's seven languages (its string tests guard them).
- Version: macOS helper 0.6.0.

## Tests

- `ScanCollection` (as in the Mac spec): order, one PDF of N pages, finish keeps the collection when the merge fails, empty finish does nothing, expiry, recovery after a "restart", damaged files, discard.
- `ButtonCollector` (fake SANE reader, fake CLI run): a press of 4, 5, 6 adds a page with the right mode, 7 finishes, the timeout finishes, a press while another scan runs is not read, no device means no polling, restart resumes an open collection, disk guard.
- Server endpoints (like `tests/server/`): list, download, delete, unknown or malformed id, no token → 401, `Origin` → refused, `buttonScans` in `/health`, older requests unchanged.
- Conformance suite (`dossiary-scan-helper/conformance`): the new calls against the reference helper's fake server mode (`fake:` button scans, a fixed list) and against `scanix500-server` with a fake collector.
- macOS helper (Swift, XCTest with a stub server): pickup saves, then deletes; failing save does not delete; a name clash gets a suffix; a repeated id is saved once; an unreachable server is skipped; a pending-OCR scan waits, then is taken after 5 minutes; the folder setting persists.
- Hardware (manual, node-000 and a real helper): press COLOR, BLACK & WHITE, CUSTOM, FINISH: one 3-page PDF appears in the chosen folder with the right modes in order and searchable text; the timeout closes a 2-page collection; restart the server with two pages collected and they are kept; pull the helper's folder away and PDFs wait on the Pi.

## Risks

- The Pi froze once when a scanner was hot-plugged (README). The collector never opens SANE unless the 8800F is already on the USB bus at start of a press; it does not make hot-plugging safe, and the README warning stays.
- The Pi's memory (3.4 GB): one page at a time is scanned, at most 600 dpi for the buttons, as one page scans today; OCR runs after finish in the background queue, one job at a time.
- Two helpers may race on the same PDF. The id and `picked-up.json` stop double saves per Mac; two Macs may each save a copy before the first `DELETE` lands. This is accepted.

## Out of scope

The iX500's own buttons beyond pairing, a push channel from the Pi, delivery into a library's Inbox by the page, editing or deleting single pages of a collection, a UI for the timeout, the 8800F's other buttons, film scans, other helpers (Windows).

## Release

scanix500 0.10.0 (Pi `.deb`, the Mac app together with the Mac spec), macOS helper 0.6.0, `PROTOCOL.md` and conformance tests in `dossiary-scan-helper`. Order: collection code and Pi server on node-000 with real presses, then the protocol text and conformance tests, then the helper, then the Mac app.
