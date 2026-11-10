# CanoScan 8800F: collect pages with the PDF buttons (scanix500 0.10.0)

Date: 2026-11-10. Scope: `scanix500-menubar` (the Mac). The Raspberry Pi part is a separate later spec (see "Raspberry Pi").

## Problem

The 8800F has three PDF scan buttons (COLOR, BLACK & WHITE, CUSTOM) and FINISH. Today each of the three buttons scans one page into its own PDF at once, and FINISH (target 7) does nothing (`PIXMA_BUTTONS` in `menubar/button_watcher.py`). The glass takes one page at a time, so a letter of several pages needs several PDFs that the user must join by hand.

## Decisions (agreed 2026-11-10)

1. **Collecting.** Each of the three scan buttons scans one page and adds it to the open collection (the first press opens it). FINISH closes the collection and delivers one PDF with the pages in order. A single page works the same way: it is delivered after FINISH or the timeout.
2. **Timeout.** After 10 minutes without a button press or menu action the collection closes by itself, as if FINISH had been pressed. Nothing is ever discarded. The 10 minutes are the default of a setting.
3. **Both places in the end.** The Mac comes first (this spec). The Pi follows as its own spec: it needs a new way to hand a PDF to Dossiary that no one asked for (see "Raspberry Pi").

## Design

**Scope of the behaviour.** Only the 8800F's buttons, and only when the "Hardware Button" profile's `format` is `pdf`. For `jpeg` or `png` the buttons keep working as today (one scan, delivered at once). The iX500's feeder, its single Scan button, menu clicks and bridge (Dossiary) scans are unchanged.

**New `ScanCollection`** (new file `src/scanix500/menubar/collection.py`, no AppKit, fully unit-testable). It owns a folder `~/Library/Application Support/scanix500/collection/` (a parameter for tests) with:
- `page-001.pdf`, `page-002.pdf`, …: one-page PDFs, scanned without OCR (the existing `scanix500 … --skip-ocr` run, into this folder instead of the destination);
- `meta.json`: `started` and `last_activity` (wall-clock seconds), the page count, and the delivery settings taken from the profile at the first press: `destination`, `skip_ocr`, `scanner`. The settings do not change if the profile is edited meanwhile.

API: `add_page(pdf_path)`, `finish() -> list[Path]` (the merged, optionally OCR'd PDF), `discard()`, `page_count`, `is_open`, `expired(now)`, and `recover()` which reads an existing folder after a restart. The state is written to disk after every change, atomically like `profiles.save_profiles`.

**Finishing.** `finish()` merges the pages with the existing `pdf_builder._merge_pdfs` into `scan_<date>_<time>.pdf` (the file name the one-page scan uses today) in the destination folder, then runs OCR once over the whole PDF with `pdf_builder.ocr_pdf` unless `skip_ocr` is set. OCR failure keeps the merged PDF without text and says so (the same rule as one page: OCR failure never loses the scan). Only after the PDF is in place is the collection folder removed. A `finish()` that fails (disk full, destination missing) keeps the collection open, so a later FINISH or the timeout tries again.

**Buttons.** `PIXMA_BUTTONS` gets button 7 as a finish action. `pixma_button_overrides` returns it as a distinct value, not as profile overrides; `_on_buttons_read` in the app routes it to `finish_collection()`. Buttons 4, 5, 6 keep their meaning (colour, black and white, the profile as it is), but start a page scan into the collection instead of a finished PDF. Each page keeps its own mode: colour and black and white pages can be mixed. A button press that arrives while a scan runs is not read, as today.

**App wiring** (`menubar/app.py`). `_start_scan` for a hardware button press of the 8800F scans one page through the busy guard into the collection and refreshes the menu. A timer (every 15 s, on the main thread, doing nothing slow) closes an expired collection by running `finish` on a worker thread under the same busy guard. At start-up `recover()` runs; an open collection continues with its timeout counted from `last_activity` (an expired one finishes at once).

**Feedback.**
- The menu title shows the page count while a collection is open ("scanix500 · 3 pages"), and the menu gets two items while open: "Finish now (N pages)" and "Discard collected pages…" (a confirmation before it deletes).
- A notification when a PDF is finished ("N pages scanned", with the file name) and when a page is added to a new collection the first time ("Collecting pages. Press FINISH when you are done.").
- A failed page scan is reported as today and not added; the collected pages stay.
- Two new strings sets in `strings.json` (all 7 languages, `tests/test_i18n.py` guards them).

**Setting.** `collect_timeout_minutes` (default 10, minimum 1) in a small JSON file `collection.json` next to `bridge_destination.txt`; there is no UI for it in this version.

## Tests

- `ScanCollection`: adding pages keeps the order; `finish()` makes one PDF with N pages in the destination, with OCR once (fake `ocr_pdf` called once), without OCR when `skip_ocr`; a failed OCR keeps the PDF; a failed merge or missing destination keeps the collection; `finish()` on an empty collection does nothing; `expired()` honours the timeout and `last_activity`; `recover()` after a "restart" returns pages and settings; a damaged `meta.json` or a page file that is not a PDF is quarantined to a `damaged/` subfolder and does not block new pages; `discard()` removes everything.
- Buttons: `_pixma_overrides` maps target 7 to the finish action and 4, 5, 6 as before, 1, 2, 3 and 0 to nothing.
- App (with `ScanixMenuBarApp.__new__` and mocked collaborators, as `tests/menubar/test_app.py`): a press adds a page and the title shows the count; FINISH delivers and clears the title; the timer finishes an expired collection and ignores a fresh one; a press while scanning is ignored; with `format` jpeg or png the old one-shot path is used; menu "Finish now" and "Discard" work.
- Hardware (manual, on the real 8800F, to be listed in the README checklist): three presses of COLOR, BLACK & WHITE and CUSTOM and one FINISH give one 3-page PDF in that order with the right modes; the timeout closes a two-page collection; quitting the app with two pages collected and starting it again keeps them.

## Raspberry Pi (not part of this spec)

Finding on 2026-11-10: `scanix500-server` never reads the 8800F's buttons. Its `ButtonWatcher` polls only the iX500's Scan button, and only while a computer waits to pair. More important, the protocol (the helper's `PROTOCOL.md`) is request and response: Dossiary asks for a scan and gets the files back. A PDF finished by a button press on the Pi has nobody to receive it. The Pi part therefore needs a protocol extension (for example a list of finished button scans that the helper or Dossiary fetches) and changes in `dossiary-scan-helper` and in Dossiary's page. It gets its own brainstorm and spec after the Mac part is released.

## Out of scope

Reordering or deleting single pages of a collection, a UI for the timeout, the iX500's feeder (it already scans many sheets in one go), several open collections, the 8800F's other buttons, film and transparency scans.

## Release

scanix500 0.10.0: tag `v0.10.0`, then `scripts/install-app.sh v0.10.0` on this Mac. README (the 8800F section, the manual checklist) and AGENTS.md get the new behaviour; the README's statement "FINISH … do nothing" changes.
