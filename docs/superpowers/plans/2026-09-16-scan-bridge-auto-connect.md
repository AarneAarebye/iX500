# Scan Bridge Auto-Connect (scanix500 side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `GET /health` probe endpoint and an in-band, base64-encoded
`files` field to the scan bridge's response, so Dossiary can auto-detect the
bridge and receive the scanned file over the connection instead of relying
on filesystem placement.

**Architecture:** Two small, additive changes to the existing
`src/scanix500/menubar/bridge.py`: a new `do_GET` route that only ever
answers `/health` (everything else 404s, nothing touches the scan trigger
or busy-guard), and a new `_encode_files()` helper that reads each path in
an already-produced `ScanResult.output_paths` back off disk and
base64-encodes it into the existing `POST /scan/<profile-name>` response.
The scan-and-write pipeline itself (`runner.run_scan()`,
`ScanixMenuBarApp._execute_scan()`/`_start_scan()`) is untouched — this
plan only adds a read-back-and-encode step after a scan has already
completed and written its file(s).

**Tech Stack:** Python 3.11+, stdlib `http.server`/`base64`/`pathlib`, `pytest`.

## Global Constraints

- The scan-and-write pipeline (`runner.py`, `app.py`'s `_execute_scan()`/
  `_start_scan()`/`trigger()`) is unchanged by this plan — every file is
  still written to the profile's own `destination` folder exactly as
  today, unconditionally, before any response is built. This is the
  design's safety net; do not make writing it conditional on anything.
- `GET /health` must never call `trigger.trigger()`, touch
  `self._scanning`, or interact with the scanner in any way — it is a
  pure reachability probe, answered directly by the HTTP handler.
- A `Profile`'s `destination` field stays required (the `Profile`
  dataclass itself, in `profiles.py`, is not touched by this plan) — only
  its *documented meaning* for the `Dossiary Scan`/`Dossiary Scan Multi`
  profiles changes (no longer needs to be a specific library's `inbox/`).
- No automatic cleanup of files written to `destination` — deliberately
  out of scope, matching this app's existing "nothing is auto-deleted"
  stance (see the design spec's own "What this removes" section).
- Follow `tests/menubar/test_bridge.py`'s existing conventions exactly:
  the `unused_tcp_port` fixture for real-socket tests, `FakeScanTrigger`
  as the test double for `ScanTrigger`, and the `_post()` helper for
  making real HTTP calls against a running server.
- Full spec: `docs/superpowers/specs/2026-09-16-scan-bridge-auto-connect-design.md`
  (in the sibling Dossiary repo, `/Users/aarneaarebye/Projects/Paperless/Dossiary`).
  This plan covers only that spec's scanix500-side pieces (the `GET
  /health` endpoint and the `files` field); the Dossiary-side auto-connect
  UI and inbox-write logic are a separate plan in that other repo.

---

### Task 1: `GET /health` endpoint

**Files:**
- Modify: `src/scanix500/menubar/bridge.py:62-125` (the `Handler` class inside `make_handler_class()`)
- Test: `tests/menubar/test_bridge.py`

**Interfaces:**
- Consumes: nothing new — no change to `route_scan_request()`, `ScanTrigger`, or `start_bridge_server()`'s signature.
- Produces: `GET /health` → `200` with JSON body `{"service": "scanix500-bridge"}`. Any other `GET` path → `404` with `{"error": "not found"}`, same shape `do_POST` already uses for an unmatched path.

- [ ] **Step 1: Write the failing tests**

Add to `tests/menubar/test_bridge.py`, after `test_bridge_server_reads_profiles_live_not_a_snapshot` (the last test in the file):

```python
def test_bridge_server_health_endpoint_returns_200(unused_tcp_port):
    trigger = FakeScanTrigger()
    server = start_bridge_server(lambda: [], trigger, port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/health", method="GET")
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            body = json.loads(resp.read())
            headers = resp.headers
    finally:
        server.shutdown()

    assert status == 200
    assert body == {"service": "scanix500-bridge"}
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert trigger.called_with is None


def test_bridge_server_get_unknown_path_is_404(unused_tcp_port):
    server = start_bridge_server(lambda: [], FakeScanTrigger(), port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/scan/Default", method="GET")
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            status = e.code
            body = json.loads(e.read())
    finally:
        server.shutdown()

    assert status == 404
    assert body == {"error": "not found"}


def test_bridge_server_options_preflight_advertises_get(unused_tcp_port):
    server = start_bridge_server(lambda: [], FakeScanTrigger(), port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/health", method="OPTIONS")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 204
            assert "GET" in resp.headers["Access-Control-Allow-Methods"]
    finally:
        server.shutdown()
```

Then **replace** the existing `test_bridge_server_unhandled_method_still_has_cors_header` test (it currently uses `GET` to prove an unhandled method still gets a `501` with CORS headers — but `GET` is about to become an explicitly handled method via `do_GET`, so it needs to test a method that's genuinely still unimplemented):

```python
def test_bridge_server_unhandled_method_still_has_cors_header(unused_tcp_port):
    # GET is now explicitly handled by do_GET (see /health and its 404
    # fallback below) -- use PUT here instead, a method this bridge
    # genuinely never implements, to keep testing
    # BaseHTTPRequestHandler's own unhandled-method fallback (the
    # send_error override above), not do_GET's own routing.
    server = start_bridge_server(lambda: [], FakeScanTrigger(), port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/scan/Default", method="PUT")
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
                headers = resp.headers
        except urllib.error.HTTPError as e:
            status = e.code
            headers = e.headers
    finally:
        server.shutdown()

    assert status == 501
    assert headers["Access-Control-Allow-Origin"] == "*"
```

- [ ] **Step 2: Run tests to verify the new/changed ones fail**

Run: `.venv/bin/pytest tests/menubar/test_bridge.py -v`
Expected: `test_bridge_server_health_endpoint_returns_200` and
`test_bridge_server_get_unknown_path_is_404` FAIL with a connection error
or a `501` (no `do_GET` exists yet, so `BaseHTTPRequestHandler`'s own
fallback handles every `GET`).
`test_bridge_server_options_preflight_advertises_get` FAILS because the
current `Access-Control-Allow-Methods` header is `"POST, OPTIONS"` (no
`GET`).
`test_bridge_server_unhandled_method_still_has_cors_header` (now using
`PUT`) should already PASS unchanged, since `PUT` was never handled
before or after this task — confirm it does.

- [ ] **Step 3: Implement `do_GET` and update the OPTIONS header**

In `src/scanix500/menubar/bridge.py`, inside the `Handler` class (right
after `do_OPTIONS`, before `do_POST`), add:

```python
        def do_GET(self) -> None:  # noqa: N802
            # A separate, deliberately trivial route from do_POST's scan
            # trigger -- this must never touch trigger/self._scanning, so a
            # caller (Dossiary) can probe "is the bridge here" without any
            # risk of firing a real scan at an unknown port.
            path = urlsplit(self.path).path
            if path == "/health":
                self._send_json(200, {"service": "scanix500-bridge"})
                return
            self._send_json(404, {"error": "not found"})
```

Then update `do_OPTIONS`'s `Access-Control-Allow-Methods` header to
advertise `GET` alongside the existing methods:

```python
        def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own naming
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Content-Length", "0")
            self.end_headers()
```

(Only the `Access-Control-Allow-Methods` line's value changes, from
`"POST, OPTIONS"` to `"GET, POST, OPTIONS"` — every other line is
unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/menubar/test_bridge.py -v`
Expected: all tests PASS, including the four from Step 1.

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/menubar/bridge.py tests/menubar/test_bridge.py
git commit -m "feat: add GET /health probe endpoint to the scan bridge"
```

---

### Task 2: `files` field — base64-encode scan output into the response

**Files:**
- Modify: `src/scanix500/menubar/bridge.py:1-51` (imports and `route_scan_request()`)
- Test: `tests/menubar/test_bridge.py`

**Interfaces:**
- Consumes: `ScanResult.output_paths` (`list[str]`, from `runner.py` — unchanged by this task).
- Produces: `route_scan_request()`'s returned body dict gains a `"files"` key: `list[{"filename": str, "content_base64": str}]`, one entry per readable path in `result.output_paths`, same order. A new helper `_encode_files(output_paths: list[str]) -> list[dict]` is added to `bridge.py` for later tasks/callers to reuse if ever needed, though nothing outside `route_scan_request()` calls it in this plan.

- [ ] **Step 1: Write the failing tests**

First, add `import base64` as the very first import line in
`tests/menubar/test_bridge.py` (alphabetically before the existing
`import json`), so the top of the file reads:

```python
import base64
import json
import socket
import threading
import urllib.error
import urllib.request
```

Then **replace** the existing
`test_route_scan_request_success_returns_200_with_scan_result_shape` test
with a version that writes a real file (needed so there's something real
to read back and encode) and asserts the new `files` key:

```python
def test_route_scan_request_success_returns_200_with_scan_result_shape(tmp_path):
    scan_file = tmp_path / "scan_1.pdf"
    scan_file.write_bytes(b"%PDF-1.4 fake pdf bytes")
    profiles = [Profile(name="Dossiary Scan", destination=str(tmp_path))]
    result = ScanResult(ok=True, partial=False, message=str(scan_file), output_paths=[str(scan_file)])
    trigger = FakeScanTrigger(result=result)

    status, body = route_scan_request(profiles, "Dossiary Scan", trigger)

    assert status == 200
    assert body == {
        "ok": True,
        "partial": False,
        "message": str(scan_file),
        "output_paths": [str(scan_file)],
        "files": [
            {
                "filename": "scan_1.pdf",
                "content_base64": base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode("ascii"),
            }
        ],
    }
    assert trigger.called_with == profiles[0]
```

Then add three new tests after it:

```python
def test_route_scan_request_unreadable_output_path_is_skipped_in_files():
    # A path in output_paths that can no longer be read (removed between
    # the scan finishing and this call, permissions, etc.) must not raise
    # -- it's just omitted from `files`. output_paths itself is
    # untouched, and the safety-net copy in the profile's own destination
    # folder remains the record of truth regardless.
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    result = ScanResult(ok=True, partial=False, message="ok", output_paths=["/tmp/scans/does-not-exist.pdf"])
    trigger = FakeScanTrigger(result=result)

    status, body = route_scan_request(profiles, "Dossiary Scan", trigger)

    assert status == 200
    assert body["output_paths"] == ["/tmp/scans/does-not-exist.pdf"]
    assert body["files"] == []


def test_route_scan_request_multi_file_result_encodes_every_file(tmp_path):
    scan_1 = tmp_path / "scan_1.pdf"
    scan_2 = tmp_path / "scan_2.pdf"
    scan_1.write_bytes(b"first document")
    scan_2.write_bytes(b"second document")
    profiles = [Profile(name="Dossiary Scan Multi", destination=str(tmp_path), split_on_blank=True)]
    result = ScanResult(ok=True, partial=False, message="2 documents", output_paths=[str(scan_1), str(scan_2)])
    trigger = FakeScanTrigger(result=result)

    status, body = route_scan_request(profiles, "Dossiary Scan Multi", trigger)

    assert status == 200
    assert body["files"] == [
        {"filename": "scan_1.pdf", "content_base64": base64.b64encode(b"first document").decode("ascii")},
        {"filename": "scan_2.pdf", "content_base64": base64.b64encode(b"second document").decode("ascii")},
    ]


def test_bridge_server_round_trip_delivers_file_bytes(tmp_path, unused_tcp_port):
    scan_file = tmp_path / "scan_1.pdf"
    scan_file.write_bytes(b"%PDF-1.4 fake pdf bytes")
    profiles = [Profile(name="Dossiary Scan", destination=str(tmp_path))]
    result = ScanResult(ok=True, partial=False, message=str(scan_file), output_paths=[str(scan_file)])
    trigger = FakeScanTrigger(result=result)
    server = start_bridge_server(lambda: profiles, trigger, port=unused_tcp_port)
    try:
        status, body = _post(f"http://127.0.0.1:{unused_tcp_port}/scan/Dossiary%20Scan")
    finally:
        server.shutdown()

    assert status == 200
    assert body["files"] == [
        {
            "filename": "scan_1.pdf",
            "content_base64": base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode("ascii"),
        }
    ]
```

`test_route_scan_request_partial_result_still_returns_200` and
`test_bridge_server_round_trip_success` both use fake, nonexistent paths
(`"/tmp/scans/scan_1.pdf"`) and only assert individual keys (`body["ok"]`,
`body["partial"]`), never the full dict — they need **no changes**; their
`files` field will simply be `[]` (nothing readable at that path), which
they don't assert on either way.

- [ ] **Step 2: Run tests to verify the new/changed ones fail**

Run: `.venv/bin/pytest tests/menubar/test_bridge.py -v`
Expected: `test_route_scan_request_success_returns_200_with_scan_result_shape`
FAILS (`KeyError`-shaped mismatch — actual body has no `"files"` key yet).
`test_route_scan_request_unreadable_output_path_is_skipped_in_files`,
`test_route_scan_request_multi_file_result_encodes_every_file`, and
`test_bridge_server_round_trip_delivers_file_bytes` all FAIL with
`KeyError: 'files'`.

- [ ] **Step 3: Implement `_encode_files()` and wire it into `route_scan_request()`**

In `src/scanix500/menubar/bridge.py`, add two new imports at the top
(alphabetical, alongside the existing ones):

```python
import base64
```

and

```python
from pathlib import Path
```

so the import block reads:

```python
from __future__ import annotations

import base64
import json
import os
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit
```

Then add `_encode_files()` right before `route_scan_request()`:

```python
def _encode_files(output_paths: list[str]) -> list[dict]:
    """Reads each path in output_paths off disk and base64-encodes it for
    the HTTP response, in the same order as output_paths. A path that can
    no longer be read (removed, permissions) is skipped rather than
    raising -- the response still carries whatever files it could read,
    and output_paths / the on-disk safety-net copy in the profile's own
    destination folder remain the authoritative record regardless (see
    the 2026-09-16 auto-connect design spec)."""
    files = []
    for path in output_paths:
        try:
            data = Path(path).read_bytes()
        except OSError:
            continue
        files.append({"filename": Path(path).name, "content_base64": base64.b64encode(data).decode("ascii")})
    return files
```

Then change `route_scan_request()`'s final return statement from:

```python
    return 200, {
        "ok": result.ok,
        "partial": result.partial,
        "message": result.message,
        "output_paths": result.output_paths,
    }
```

to:

```python
    return 200, {
        "ok": result.ok,
        "partial": result.partial,
        "message": result.message,
        "output_paths": result.output_paths,
        "files": _encode_files(result.output_paths),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/menubar/test_bridge.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -q`
Expected: all tests PASS (this task only touches `bridge.py`, but confirm
nothing elsewhere imports `route_scan_request()`'s return shape in a way
that assumed exactly four keys).

- [ ] **Step 6: Commit**

```bash
git add src/scanix500/menubar/bridge.py tests/menubar/test_bridge.py
git commit -m "feat: base64-encode scan output files into the bridge response"
```

---

### Task 3: Documentation — README.md and CLAUDE.md

**Files:**
- Modify: `README.md` (Phase 4 section, its manual checklist, and the "Dossiary integration" section)
- Modify: `CLAUDE.md` (the HTTP bridge bullet)

**Interfaces:**
- Consumes: the `GET /health` endpoint and `files` field from Tasks 1-2 (documentation only — no code in this task).
- Produces: nothing consumed by later tasks; this is the plan's last task.

- [ ] **Step 1: Update the JSON response example and add the `files` explanation in README.md**

In `README.md`, replace the existing JSON example (currently on its own
line, roughly line 354):

```
{"ok": true, "partial": false, "message": "/path/to/scan.pdf", "output_paths": ["/path/to/scan.pdf"]}
```

with:

```
{"ok": true, "partial": false, "message": "/path/to/scan.pdf", "output_paths": ["/path/to/scan.pdf"], "files": [{"filename": "scan.pdf", "content_base64": "JVBERi0xLjQK..."}]}
```

Then, immediately after the existing paragraph that ends "...a `409` means
a scan is already in progress (from any trigger — menu, hardware button,
or another bridge request) and this request was rejected immediately, not
queued.", add a new paragraph:

```markdown
**`files` carries the same file(s) named in `output_paths`, base64-encoded**,
so a caller (like Dossiary) doesn't need filesystem access to the
destination folder at all — it can decode each `content_base64` entry and
write the bytes wherever it needs them. `output_paths` is still the
on-disk record: every file is written to the profile's own `destination`
folder as an unconditional safety net *before* this response is built,
regardless of whether the caller ever reads `files`. A path that can no
longer be read by the time the response is built (removed, permissions)
is simply omitted from `files` rather than failing the whole response.
```

- [ ] **Step 2: Document `GET /health` in README.md**

In `README.md`, immediately after the smoke-test paragraph (the one
ending with the `curl -i -X POST http://127.0.0.1:8765/scan/Default` code
block), add:

```markdown
**`GET /health`** is a separate, lightweight endpoint a caller can probe
before ever attempting a scan — `200` with `{"service":
"scanix500-bridge"}` immediately, no busy-guard interaction, no scanner
involvement. It exists so a client can auto-detect whether the bridge is
running on the default port before falling back to asking the person to
confirm the port manually:

    curl -i http://127.0.0.1:8765/health
```

- [ ] **Step 3: Update the "Dossiary integration" section in README.md**

In `README.md`, replace the paragraph currently reading:

```markdown
Create both once via **Add Profile…** in the menu bar app, with their
destination set to your Dossiary library's actual `inbox/` folder (e.g.
`/path/to/your/library/inbox`) — Dossiary's own "Check inbox" flow picks
up whatever lands there. These names are a fixed contract Dossiary's own
code depends on; renaming either profile breaks the integration until
Dossiary's own setting is updated to match, or the profile is renamed
back.
```

with:

```markdown
Create both once via **Add Profile…** in the menu bar app. Their
`destination` folder no longer needs to point at any particular Dossiary
library — Dossiary receives the scanned file directly over the bridge
connection (see the `files` field above) and writes it into whichever
library's `inbox/` is currently open, so `destination` here is just a
local safety-net copy; point it anywhere you like (your Desktop, a
dedicated scans folder — it's never read by Dossiary). These names are
still a fixed contract Dossiary's own code depends on; renaming either
profile breaks the integration until Dossiary's own setting is updated to
match, or the profile is renamed back.
```

- [ ] **Step 4: Add manual checklist items in README.md**

In the "Manual UI checklist" section, immediately after the existing item
"...the response is `200` with a JSON body naming the output PDF path."
(around line 270), add two new checklist items:

```markdown
- [ ] The same response's `files` field contains a `content_base64` entry
      for that PDF — decode it locally (e.g.
      `python3 -c "import base64,sys; open('out.pdf','wb').write(base64.b64decode(sys.stdin.read()))"`,
      piping the field's value in) and confirm it opens as a valid PDF.
- [ ] `curl -i http://127.0.0.1:8765/health` → `200` with
      `{"service": "scanix500-bridge"}`, and the menu bar icon does
      **not** change to its scanning state (proves the probe never
      touches the scanner).
```

- [ ] **Step 5: Update CLAUDE.md**

In `CLAUDE.md`, immediately after the existing HTTP bridge bullet's last
sentence ("...profiles named exactly `"Dossiary Scan"`/`"Dossiary Scan
Multi"` must exist for its toolbar buttons to work — see README.md's own
"Dossiary integration" section."), add a new bullet:

```markdown
- **Extended 2026-09-16** (see
  `docs/superpowers/specs/2026-09-16-scan-bridge-auto-connect-design.md`,
  plan `docs/superpowers/plans/2026-09-16-scan-bridge-auto-connect.md`):
  the bridge gained a `GET /health` endpoint — a lightweight reachability
  probe, separate from the scan-triggering `POST /scan/<profile>`, so a
  caller can check the bridge is there without ever risking a real scan
  against an unknown port — and the `POST /scan/<profile>` response
  gained a `files` field: each file named in `output_paths` is also read
  back and base64-encoded into the response, so a caller like Dossiary
  receives the scanned bytes directly over the connection instead of
  relying on `destination` matching a location it can read. `destination`
  remains required (the scan pipeline still needs somewhere to write) but
  is now purely a local safety-net copy — it no longer needs to point at
  any particular Dossiary library's `inbox/`.
```

- [ ] **Step 6: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: document GET /health and the files field; update Dossiary integration notes"
```
