# Photos from the CanoScan 8800F: image formats, scan areas, 1200 dpi

Date: 2026-10-24
Repos: dossiary-scan-helper (protocol additions, conformance suite, reference
helper first; the macOS helper later), iX500 (this repo: server, bridge, CLI),
Dossiary (the scan dialog, later)

This is sub-project 2 of three for the CanoScan 8800F (1: flatbed documents,
done in 0.8.0; 3: film and slides, later). Of the three repos' parts below,
**only part 1 is designed in detail here**; parts 2 and 3 get their own spec,
plan and release once part 1 has settled the protocol.

## Goal

Scan a photograph (or any small print) on the 8800F's glass and get it back as
a JPEG or PNG at up to 1200 dpi, scanning only the area the print covers, from
Dossiary through the helper and the Pi server like any other scan. PDF stays
the default for everything.

## Decisions (made while designing, with the reasons)

- **JPEG and PNG, not TIFF.** Dossiary already imports JPEG and PNG as
  documents (and makes a searchable PDF of them with its own OCR, keeping the
  original); it has no TIFF import.
- **A scan-area field in the protocol** (`area`), because a photo covers a
  fraction of the glass and scanning all of A4 at photo resolutions is slow and
  does not fit in the Pi's memory (A4 at 1200 dpi is about 417 MB raw in color).
  Cropping afterwards was rejected: the lid is white, so edge detection on
  white-bordered prints is unreliable, and the full area would still be scanned
  first. An earlier note (spec 2026-10-09, `snap.py` comments) already said a paper
  size choice "would need a protocol field": this is that field.
- **Resolutions up to 1200 dpi, with a pixel budget.** 150, 300, 600, 1200. Higher
  resolutions are for film (sub-project 3).
- **The 8800F only.** The iX500's feeder keeps PDF only (one image per page and
  two files per sheet raise questions nobody needs answered).

## Part 1a: the protocol additions (`PROTOCOL.md` in dossiary-scan-helper)

Everything is optional and additive. The protocol version stays 2: a client that
doesn't know `formats` or `areas` never sends `format` or `area` and keeps
getting PDFs, and a scanner without them behaves as today.

**Scanner entry** (`GET /scanners`):

| field | type | meaning |
|---|---|---|
| `formats` | array of `"pdf"` / `"jpeg"` / `"png"`, optional | The output formats. Absent means `["pdf"]`. Always includes `"pdf"`, the default of a request without `format`. |
| `areas` | array of area ids, optional | The scan areas it offers. Absent: the scanner decides (feeders). The first is the default. |

**Area ids**, sized in millimetres, upright (portrait) and anchored at the glass's
corner mark: `a4` 210 x 297, `a5` 148 x 210, `a6` 105 x 148, `10x15` 100 x 150,
`13x18` 130 x 180. A scanner lists only the ids it offers.

**Request** (`POST /scan`): `format` (string, default `"pdf"`) and `area` (string, default the scanner's first area). Validation keeps its
order (PROTOCOL.md "Request validation"): wrong JSON types are 400 at step 4, and at
step 5 these are **422**, each with its own message:

- `format` not in the scanner's `formats`;
- `area` not in the scanner's `areas`, or `area` for a scanner without `areas`;
- `ocr: true` with an image format (Dossiary does the OCR of an imported image
  itself);
- `colorMode: "bw"` with `format: "jpeg"` (JPEG can't be 1-bit; PNG and PDF can);
- an area and resolution whose pixel count is over 100,000,000, with the message
  telling the person what to change, for example "A4 at 1200 dpi is too large
  (139 megapixels; the limit is 100): choose a smaller area or a lower resolution."
  (The limit is the server's, not a protocol field: clients just show the message.)

**Response**: each file is one image: `pages: 1`, `name` ending in `.jpg` or `.png`
(PDFs keep `.pdf`), `data` the base64 file. No `ocrJob`. The `message` for an
image scan is "1 image scanned." (PDFs: "N pages scanned.").

**The 8800F's entry** becomes `formats: ["pdf","jpeg","png"]`, `areas:
["a4","a5","a6","10x15","13x18"]`, `resolutions: [150,300,600,1200]`. The iX500's
entry is unchanged.

**Fake scanner mode** (conformance suite): a new `fake:photo` scanner, flatbed,
formats pdf/jpeg/png, areas a4/10x15, answering an image request with a tiny valid
JPEG or PNG; the suite gains tests for each 422 above and for the response shape.
The reference helper in `reference/` implements the same.

## Part 1b: this repo (scanix500)

- **`scanners.py`**: the 8800F entry changes as above; an `AREAS` table (id to
  `(width_mm, height_mm)`); `MAX_PIXELS = 100_000_000`; `pixel_count(area, dpi)`
  from the area's millimetres and the dpi.
- **`protocol_v2.validate`**: the checks above, in that order after the existing
  ones; the returned settings carry `format` and `area`. A scanner entry without
  `formats` is treated as `["pdf"]`, without `areas` as "no area".
- **The scan travels as before**: `Profile` gains `format` (default `"pdf"`) and
  `area` (default `"a4"`); `profile_for` fills them; `runner._build_argv` adds
  `--format jpeg` and `--area 10x15` only when they aren't the defaults; the CLI
  takes `--format {pdf,jpeg,png}` and `--area <id>`. Old `profiles.json` files load
  unchanged.
- **Capture**: `PySaneFlatbed(area=...)` sets the scan window to the area's size
  with the top-left at 0, 0, instead of the fixed A4.
- **Encoding**: a new `images.py`, `save_image(image, path, fmt, dpi)`, writes JPEG
  (quality 92, dpi embedded) or PNG (1-bit for black & white, dpi embedded). The
  CLI's flatbed branch uses it instead of `build_pdf` when `--format` is an image;
  `router.compute_output_paths` takes the file extension. The iX500 path refuses an
  image format (exit 1) as a second line of defence; validation never lets one reach
  it. No trim, snap or blank filter for flatbed pages (unchanged).
- **Response**: `protocol_v2._file_entry` returns `pages: 1` for an image instead of
  asking `PdfReader`; `response_from_result` words the message per file type.
- **Unchanged**: the Mac menu bar app's own menu and buttons (the Hardware Button profile is
  PDF, A4; its HTTP bridge shares `RealBackend` and so gets the new behaviour for free), the iX500, the OCR job queue, `scanix500-server`'s pairing and button
  handling.
- **Documentation**: README and AGENTS.md; the version becomes **0.9.0**.

## Part 2 and 3 (later, their own specs)

2. **dossiary-scan-helper (macOS helper)**: forward `format` and `area` to the
   Pi server and pass the new scanner fields through. The helper's own code must be
   read first; nothing is assumed here.
3. **Dossiary**: the scan dialog shows "Format" and "Paper size" only when the
   chosen scanner lists them, sends them, imports the returned JPEG or PNG as a
   document, and stops assuming every scan file is a PDF (page counting, the
   `ocrJob` handling).

## Risks and how they are verified

- **Memory on the Pi (3.8 GB, about 3.4 GB available).** The 100 MP budget assumes a
  peak of about three times the raw size (python-sane's `scan()` buffer, the
  `bytes` copy, PIL). Measure the real peak RSS on node-000 for 13 x 18 cm at 1200 dpi
  (52 MP) and A5 at 1200 dpi (70 MP); lower the budget if it is too high.
- **The glass's origin.** `area` assumes (0, 0) is the corner mark at the rear left;
  verify with a real 10 x 15 cm print that fills the scan.
- **Scan time at 1200 dpi** is probably minutes. The request already has no timeout
  (PROTOCOL.md); check it holds through the helper and the Pi.
- **JPEG/PNG dpi metadata** is read by viewers: check a scan reports its dpi.

## Testing

Automated: validation (every 422 and the budget boundary exactly at and over 100 MP),
`pixel_count`, `images.py` (JPEG and PNG signatures, dpi, 1-bit PNG),
the CLI flatbed image branch (mocked at `capture_pages` as today), the profile and
runner round trips, `_file_entry` for images, `GET /scanners` fields, and the fake
backend's `fake:photo`. The conformance run (`scripts/run-conformance.sh`) must stay
green and, once the suite has the new tests, pass them.

Manual, on real hardware (not mocked): the Mac and the Pi with a real print; peak
memory; scan time; origin; metadata.

## Release and order

1. PROTOCOL.md, the conformance suite and the reference helper (dossiary-scan-helper).
2. This repo, 0.9.0 (tag `v0.9.0`, `scripts/install-app.sh v0.9.0`, the `.deb`).
3. The macOS helper, then Dossiary.

Nothing in 1 or 2 breaks a client that doesn't know the new fields.

## Out of scope

TIFF; film and slides (sub-project 3); a custom area in millimetres; landscape
placement; image formats on the iX500; OCR for images; any change to the 8800F's
buttons (its PHOTO/FILM button could later start a photo scan).
