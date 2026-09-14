# scanix500 HTTP Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an external caller (Dossiary's browser JS) trigger a real scan on the physical iX500 over local HTTP, by name, reusing the menu bar app's existing profile system and busy-guard.

**Architecture:** A new `bridge.py` module implements the HTTP layer as pure, framework-independent routing logic (`route_scan_request`) plus a thin `http.server.ThreadingHTTPServer` wrapper, decoupled from `ScanixMenuBarApp`/rumps/AppKit via a small `ScanTrigger` protocol. `ScanixMenuBarApp` implements that protocol by marshaling onto the main thread (via `AppHelper.callAfter`, mirroring the app's existing thread-safety pattern) to check-and-set the shared `_scanning` guard, run the scan, and update the icon/notification through the exact same `_on_scan_complete()` path a menu click or the hardware button already uses — then blocks the calling (HTTP request) thread until that completes, so the bridge's response reflects the real outcome, not just "started."

**Tech Stack:** Python 3.11+, `http.server`/`threading` (stdlib, no new dependency), `pytest`, `urllib.request` for round-trip tests.

## Global Constraints

- No new dependency — `http.server` is stdlib; Python 3.11+ is already this repo's floor.
- The bridge binds to `127.0.0.1` only, never `0.0.0.0` — single-machine trust boundary, not a networked service.
- Port defaults to `8765`, overridable via the `SCANIX500_BRIDGE_PORT` environment variable.
- Every HTTP response — success or error — includes `Access-Control-Allow-Origin: *`; `OPTIONS` requests get a handled `204` preflight response, never a `404`/`501`.
- The bridge's scan-trigger path MUST go through the exact same `self._scanning` busy-guard and the exact same `_on_scan_complete()` notification/icon-reset path that `_start_scan()`/`_run_scan_thread()` already use for menu clicks and the hardware button — not a parallel copy. A Dossiary-triggered scan must be indistinguishable, from the menu bar app's own perspective, from any other trigger.
- No authentication, no token, no origin allow-list narrower than `*` — accepted trust model per the design spec; binding to `127.0.0.1` is the sole protection.
- Fixed profile-name contract Dossiary depends on: `"Dossiary Scan"` and `"Dossiary Scan Multi"` — documented in README.md/CLAUDE.md as a one-time manual setup step, never auto-created by this plan's code.
- Hardware/live-GUI code gets no automated tests, matching this repo's existing precedent (`button_watcher.py`, `app.py` have none today) — verified via a manual checklist item instead. Pure logic (routing, JSON shaping, the busy-guard semantics, `_execute_scan`'s exception handling) gets full automated coverage.

---

### Task 1: Pure scan-request routing logic (`bridge.py`, no HTTP yet)

**Files:**
- Create: `src/scanix500/menubar/bridge.py`
- Test: `tests/menubar/test_bridge.py`

**Interfaces:**
- Produces: `scanix500.menubar.bridge.ScanBusyError` (exception, no args beyond the standard `Exception` ones). `scanix500.menubar.bridge.ScanTrigger` (a `typing.Protocol` with `def trigger(self, profile: Profile) -> ScanResult: ...` — raises `ScanBusyError` if a scan is already in progress). `scanix500.menubar.bridge.route_scan_request(profiles: list[Profile], name: str, trigger: ScanTrigger) -> tuple[int, dict]` — pure dispatch: profile lookup, `ScanTrigger.trigger()` call, response shaping. Used by Task 2's HTTP handler and Task 3's `ScanixMenuBarApp.trigger()` implementation.
- Consumes: `scanix500.menubar.profiles.Profile`, `find_profile` (existing, `src/scanix500/menubar/profiles.py:84`). `scanix500.menubar.runner.ScanResult` (existing, `src/scanix500/menubar/runner.py:14-19`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/menubar/test_bridge.py
from scanix500.menubar.bridge import ScanBusyError, route_scan_request
from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult


class FakeScanTrigger:
    """Test double for bridge.ScanTrigger -- records the profile it was
    called with and returns/raises whatever the test configures, with no
    real threading, rumps, or AppKit involved."""

    def __init__(self, result: ScanResult | None = None, raises: Exception | None = None):
        self.result = result
        self.raises = raises
        self.called_with: Profile | None = None

    def trigger(self, profile: Profile) -> ScanResult:
        self.called_with = profile
        if self.raises is not None:
            raise self.raises
        return self.result


def test_route_scan_request_unknown_profile_returns_404():
    profiles = [Profile(name="Default", destination="/tmp/scans")]
    trigger = FakeScanTrigger()

    status, body = route_scan_request(profiles, "Nonexistent", trigger)

    assert status == 404
    assert "Nonexistent" in body["error"]
    assert trigger.called_with is None


def test_route_scan_request_busy_returns_409():
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    trigger = FakeScanTrigger(raises=ScanBusyError())

    status, body = route_scan_request(profiles, "Dossiary Scan", trigger)

    assert status == 409
    assert "already in progress" in body["error"]


def test_route_scan_request_success_returns_200_with_scan_result_shape():
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    result = ScanResult(ok=True, partial=False, message="/tmp/scans/scan_1.pdf", output_paths=["/tmp/scans/scan_1.pdf"])
    trigger = FakeScanTrigger(result=result)

    status, body = route_scan_request(profiles, "Dossiary Scan", trigger)

    assert status == 200
    assert body == {
        "ok": True,
        "partial": False,
        "message": "/tmp/scans/scan_1.pdf",
        "output_paths": ["/tmp/scans/scan_1.pdf"],
    }
    assert trigger.called_with == profiles[0]


def test_route_scan_request_partial_result_still_returns_200():
    profiles = [Profile(name="Dossiary Scan Multi", destination="/tmp/scans", split_on_blank=True)]
    result = ScanResult(
        ok=False,
        partial=True,
        message="Multi-feed detected at sheet 3 — partial scan saved to /tmp/scans/scan_1.pdf",
        output_paths=["/tmp/scans/scan_1.pdf"],
    )
    trigger = FakeScanTrigger(result=result)

    status, body = route_scan_request(profiles, "Dossiary Scan Multi", trigger)

    assert status == 200
    assert body["ok"] is False
    assert body["partial"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/menubar/test_bridge.py -v`
Expected: FAIL with `ImportError: cannot import name 'ScanBusyError'` (module doesn't exist yet)

- [ ] **Step 3: Implement the pure routing logic**

```python
# src/scanix500/menubar/bridge.py
from __future__ import annotations

from typing import Protocol

from scanix500.menubar.profiles import Profile, find_profile
from scanix500.menubar.runner import ScanResult

DEFAULT_PORT = 8765


class ScanBusyError(Exception):
    """Raised by a ScanTrigger.trigger() call when a scan is already
    running -- maps to an HTTP 409 in route_scan_request, never to a
    queued/blocked request."""


class ScanTrigger(Protocol):
    """Something that can run a named profile's scan and block until it
    completes (or raise ScanBusyError), returning the real ScanResult --
    not just "started". Implemented by ScanixMenuBarApp (see app.py,
    Task 3) for the real app; FakeScanTrigger (tests/menubar/test_bridge.py)
    stands in for tests. Deliberately has no rumps/AppKit/threading
    dependency in this file -- that all lives in app.py's implementation."""

    def trigger(self, profile: Profile) -> ScanResult: ...


def route_scan_request(profiles: list[Profile], name: str, trigger: ScanTrigger) -> tuple[int, dict]:
    """Pure dispatch: look up `name` in `profiles`, run it via `trigger`,
    shape the response. No HTTP-specific code and no threading here --
    Task 2's HTTP handler and Task 3's ScanixMenuBarApp.trigger() are the
    only two things that need to know this exists."""
    profile = find_profile(profiles, name)
    if profile is None:
        return 404, {"error": f"no profile named {name!r}"}
    try:
        result = trigger.trigger(profile)
    except ScanBusyError:
        return 409, {"error": "a scan is already in progress"}
    return 200, {
        "ok": result.ok,
        "partial": result.partial,
        "message": result.message,
        "output_paths": result.output_paths,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/menubar/test_bridge.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/menubar/bridge.py tests/menubar/test_bridge.py
git commit -m "Add pure scan-request routing logic for the HTTP bridge"
```

---

### Task 2: HTTP server wiring (`ThreadingHTTPServer`, CORS, OPTIONS)

**Files:**
- Modify: `src/scanix500/menubar/bridge.py`
- Test: `tests/menubar/test_bridge.py`

**Interfaces:**
- Consumes: Task 1's `route_scan_request`, `ScanTrigger`, `DEFAULT_PORT`.
- Produces: `scanix500.menubar.bridge.make_handler_class(get_profiles: Callable[[], list[Profile]], trigger: ScanTrigger) -> type[BaseHTTPRequestHandler]`. `scanix500.menubar.bridge.start_bridge_server(get_profiles: Callable[[], list[Profile]], trigger: ScanTrigger, port: int | None = None) -> ThreadingHTTPServer` — starts serving in a daemon thread, returns the live server (callers can `.shutdown()` it). Used by Task 3 (`ScanixMenuBarApp.__init__`) and this task's own round-trip tests.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/menubar/test_bridge.py
import json
import threading
import urllib.error
import urllib.request

from scanix500.menubar.bridge import start_bridge_server


def _post(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="POST", data=b"")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_bridge_server_round_trip_success(unused_tcp_port):
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    result = ScanResult(ok=True, partial=False, message="/tmp/scans/scan_1.pdf", output_paths=["/tmp/scans/scan_1.pdf"])
    trigger = FakeScanTrigger(result=result)
    server = start_bridge_server(lambda: profiles, trigger, port=unused_tcp_port)
    try:
        status, body = _post(f"http://127.0.0.1:{unused_tcp_port}/scan/Dossiary%20Scan")
    finally:
        server.shutdown()

    assert status == 200
    assert body["ok"] is True
    assert trigger.called_with == profiles[0]


def test_bridge_server_round_trip_unknown_profile_is_404(unused_tcp_port):
    server = start_bridge_server(lambda: [], FakeScanTrigger(), port=unused_tcp_port)
    try:
        status, body = _post(f"http://127.0.0.1:{unused_tcp_port}/scan/Nope")
    finally:
        server.shutdown()

    assert status == 404


def test_bridge_server_round_trip_busy_is_409(unused_tcp_port):
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    trigger = FakeScanTrigger(raises=ScanBusyError())
    server = start_bridge_server(lambda: profiles, trigger, port=unused_tcp_port)
    try:
        status, _ = _post(f"http://127.0.0.1:{unused_tcp_port}/scan/Dossiary%20Scan")
    finally:
        server.shutdown()

    assert status == 409


def test_bridge_server_response_has_cors_header(unused_tcp_port):
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    result = ScanResult(ok=True, partial=False, message="ok", output_paths=[])
    server = start_bridge_server(lambda: profiles, FakeScanTrigger(result=result), port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/scan/Dossiary%20Scan", method="POST", data=b"")
        with urllib.request.urlopen(req) as resp:
            assert resp.headers["Access-Control-Allow-Origin"] == "*"
    finally:
        server.shutdown()


def test_bridge_server_options_preflight_is_handled(unused_tcp_port):
    server = start_bridge_server(lambda: [], FakeScanTrigger(), port=unused_tcp_port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{unused_tcp_port}/scan/Dossiary%20Scan", method="OPTIONS")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 204
            assert resp.headers["Access-Control-Allow-Origin"] == "*"
    finally:
        server.shutdown()


def test_bridge_server_reads_profiles_live_not_a_snapshot(unused_tcp_port):
    # get_profiles is called fresh on every request, not captured once at
    # start_bridge_server() time -- proves a profile added via Add Profile
    # while the bridge is already running is immediately reachable.
    profiles = []
    server = start_bridge_server(lambda: profiles, FakeScanTrigger(result=ScanResult(True, False, "ok", [])), port=unused_tcp_port)
    try:
        status, _ = _post(f"http://127.0.0.1:{unused_tcp_port}/scan/Late")
        assert status == 404
        profiles.append(Profile(name="Late", destination="/tmp/scans"))
        status, _ = _post(f"http://127.0.0.1:{unused_tcp_port}/scan/Late")
        assert status == 200
    finally:
        server.shutdown()
```

Add this fixture at the top of the test file, alongside the existing imports:

```python
import socket

import pytest


@pytest.fixture
def unused_tcp_port() -> int:
    """A real free TCP port on 127.0.0.1, found by binding to port 0 and
    reading back what the OS assigned -- avoids hardcoding a port number
    that a previous test run's server might still be shutting down on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/menubar/test_bridge.py -v`
Expected: FAIL with `ImportError: cannot import name 'start_bridge_server'`

- [ ] **Step 3: Implement the HTTP server wrapper**

```python
# append to src/scanix500/menubar/bridge.py
import json
import os
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote


def make_handler_class(
    get_profiles: Callable[[], list[Profile]], trigger: ScanTrigger
) -> type[BaseHTTPRequestHandler]:
    """Builds a BaseHTTPRequestHandler bound to a live profiles getter (a
    zero-arg callable, not a static list -- profiles.json can change via
    Add/Edit/Delete Profile while the bridge is running, and every request
    must see the current list) and a ScanTrigger."""

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, body: dict) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own naming
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            prefix = "/scan/"
            if not self.path.startswith(prefix):
                self._send_json(404, {"error": "not found"})
                return
            name = unquote(self.path[len(prefix):])
            status, body = route_scan_request(get_profiles(), name, trigger)
            self._send_json(status, body)

        def log_message(self, format: str, *args: object) -> None:
            # scanix500-menubar has no console under launchd -- keep quiet
            # rather than writing to a stderr nothing reads.
            pass

    return Handler


def start_bridge_server(
    get_profiles: Callable[[], list[Profile]], trigger: ScanTrigger, port: int | None = None
) -> ThreadingHTTPServer:
    """Starts the bridge listening on 127.0.0.1:<port> in a daemon thread
    and returns the live server. port resolution order: this parameter (if
    given) > SCANIX500_BRIDGE_PORT env var > DEFAULT_PORT."""
    resolved_port = port if port is not None else int(os.environ.get("SCANIX500_BRIDGE_PORT", DEFAULT_PORT))
    handler_cls = make_handler_class(get_profiles, trigger)
    server = ThreadingHTTPServer(("127.0.0.1", resolved_port), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/menubar/test_bridge.py -v`
Expected: PASS (10 tests total)

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/menubar/bridge.py tests/menubar/test_bridge.py
git commit -m "Add HTTP server wiring for the scan bridge (CORS, OPTIONS, live routing)"
```

---

### Task 3: Wire the bridge into `ScanixMenuBarApp`

**Files:**
- Modify: `src/scanix500/menubar/app.py`
- Test: `tests/menubar/test_app.py` (new file — this repo currently has none, since `app.py` is live-GUI code with no automated coverage by design; this task adds the one piece of `app.py` that genuinely is pure logic)

**Interfaces:**
- Consumes: Task 2's `start_bridge_server`, `ScanBusyError` (Task 1). Existing `ScanixMenuBarApp.__init__` (`app.py:55-71`), `_on_scan_complete` (`app.py:155-158`), `_make_scan_handler`/`_poll_button` (`app.py:117-140`, unmodified — both call `_start_scan(profile)` with no `on_done`, which keeps working unchanged against the new signature's default), `run_scan`/`ScanResult` (`runner.py`).
- Produces: `ScanixMenuBarApp._start_scan(profile: Profile, on_done: Callable[[ScanResult], None] | None = None) -> bool` (changed signature — now returns whether it actually started, and accepts an optional completion callback; the two pre-existing call sites are unaffected since both arguments are optional/ignorable). `ScanixMenuBarApp._execute_scan(profile: Profile) -> ScanResult` (extracted from the existing `_run_scan_thread` body — converts any exception to a failed `ScanResult`, never raises). `ScanixMenuBarApp.trigger(profile: Profile) -> ScanResult` (the `ScanTrigger` implementation the bridge calls — reuses `_start_scan()` itself for the busy-guard, blocks the calling thread until the scan resolves or raises `ScanBusyError`).

- [ ] **Step 1: Write the failing test for the one pure piece of this task**

```python
# tests/menubar/test_app.py
from unittest.mock import patch

from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult


def test_execute_scan_converts_exception_to_failed_scan_result():
    # _execute_scan must never raise -- an uncaught exception here would
    # (in the real _run_scan_thread caller) strand _scanning=True forever,
    # since nothing downstream would ever reset it.
    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)  # skip rumps.App.__init__/AppKit setup
    with patch("scanix500.menubar.app.run_scan", side_effect=RuntimeError("SANE backend crashed")):
        result = app._execute_scan(Profile(name="Default", destination="/tmp/scans"))

    assert result == ScanResult(ok=False, partial=False, message="SANE backend crashed", output_paths=[])


def test_execute_scan_returns_run_scan_result_unchanged_on_success():
    from scanix500.menubar.app import ScanixMenuBarApp

    app = ScanixMenuBarApp.__new__(ScanixMenuBarApp)
    expected = ScanResult(ok=True, partial=False, message="/tmp/scans/scan_1.pdf", output_paths=["/tmp/scans/scan_1.pdf"])
    with patch("scanix500.menubar.app.run_scan", return_value=expected):
        result = app._execute_scan(Profile(name="Default", destination="/tmp/scans"))

    assert result == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/menubar/test_app.py -v`
Expected: FAIL with `AttributeError: 'ScanixMenuBarApp' object has no attribute '_execute_scan'`

- [ ] **Step 3: Extract `_execute_scan`, add `trigger()`, start the bridge**

Replace `app.py`'s existing `_start_scan`/`_run_scan_thread` (`app.py:110-115` and `142-153`) with:

```python
    def _start_scan(self, profile: Profile, on_done: Callable[[ScanResult], None] | None = None) -> bool:
        """Returns True if a scan was actually started, False if one was
        already in progress (in which case on_done is never called). The
        two pre-existing callers (_make_scan_handler's menu-click handler,
        _poll_button's hardware-button path) both ignore the return value
        and never pass on_done -- unchanged behavior for them. trigger()
        (below) is the one new caller that uses both."""
        if self._scanning:
            return False
        self._scanning = True
        self.title = SCANNING_TITLE
        threading.Thread(target=self._run_scan_thread, args=(profile, on_done), daemon=True).start()
        return True

    def _execute_scan(self, profile: Profile) -> ScanResult:
        # Any exception here must never propagate -- _run_scan_thread below
        # would otherwise strand _scanning=True forever, since nothing
        # downstream would ever reset it. Pure logic, no rumps/AppKit
        # dependency -- see tests/menubar/test_app.py.
        try:
            return run_scan(profile)
        except Exception as e:  # noqa: BLE001 - must never wedge the app
            return ScanResult(ok=False, partial=False, message=str(e), output_paths=[])

    def _run_scan_thread(self, profile: Profile, on_done: Callable[[ScanResult], None] | None = None):
        result = self._execute_scan(profile)
        # rumps.Timer.start() schedules onto NSRunLoop.currentRunLoop(), which
        # from this worker thread is a run loop nothing ever runs — the
        # callback would never fire. AppHelper.callAfter marshals onto the
        # main thread's run loop from any thread.
        AppHelper.callAfter(self._on_scan_complete, result)
        if on_done is not None:
            on_done(result)

    def trigger(self, profile: Profile) -> ScanResult:
        """ScanTrigger implementation (see bridge.py) -- called from an HTTP
        request-handler thread (ThreadingHTTPServer spawns one per
        request), never the main thread. Must never touch self.title or
        call rumps.notification directly from here (AppKit calls are only
        safe on the main thread), so the actual check-and-set/scan-start
        happens inside _start_scan(), called via AppHelper.callAfter on
        the main thread -- the exact same function (not a re-implementation
        of its guard) that a menu click or the hardware button already
        calls, so a concurrent bridge request and hardware-button press
        can't both pass the busy check before either sets self._scanning.
        Blocks the calling thread until the scan resolves (or the
        busy-guard fires), so the bridge's HTTP response reflects the real
        ScanResult, not just "started"."""
        done = threading.Event()
        result_box: list[ScanResult | None] = [None]
        started_box: list[bool] = [False]

        def on_done(result: ScanResult):
            result_box[0] = result
            done.set()

        def on_main_thread():
            started_box[0] = self._start_scan(profile, on_done=on_done)
            if not started_box[0]:
                done.set()

        AppHelper.callAfter(on_main_thread)
        done.wait()
        if not started_box[0]:
            raise ScanBusyError()
        return result_box[0]
```

Add these imports at the top of `app.py`, alongside the existing ones:

```python
from collections.abc import Callable

from scanix500.menubar.bridge import ScanBusyError, start_bridge_server
```

In `ScanixMenuBarApp.__init__` (`app.py:55-71`), right after the existing `self._button_timer.start()` line, add:

```python
        # Embedded HTTP bridge (see bridge.py) -- lets an external caller
        # (Dossiary's browser JS) trigger a scan by profile name over
        # local HTTP. self.profiles is read fresh on every bridge request
        # (via the lambda), not snapshotted here, so Add/Edit/Delete
        # Profile while the bridge is running is picked up immediately.
        self._bridge_server = start_bridge_server(lambda: self.profiles, self)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/menubar/test_app.py -v`
Expected: PASS (2 tests)

Run the full suite to confirm nothing else broke: `.venv/bin/pytest -v`
Expected: PASS (all tests, including the pre-existing `tests/menubar/test_runner.py`/`test_profiles.py` and Task 1/2's `test_bridge.py`)

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/menubar/app.py tests/menubar/test_app.py
git commit -m "Wire the HTTP bridge into ScanixMenuBarApp, sharing the existing busy-guard"
```

- [ ] **Step 6: Manual verification against a running menu bar app**

This step has no automated test — `app.py`'s actual AppKit/rumps threading behavior can only be verified live, matching this repo's existing precedent for `button_watcher.py`/`app.py`. Add this checklist item to README.md's own "Manual UI checklist" section under Phase 2 (see Task 4), and run it once now:

1. `.venv/bin/scanix500-menubar` (with a profile named e.g. `"Default"` already present, or let it seed one).
2. In a second terminal: `curl -i -X POST http://127.0.0.1:8765/scan/Default` — with paper loaded in the ADF, confirm the menu bar icon changes to the scanning state, the request blocks until the scan completes, and the response is `200` with a JSON body naming the output PDF path.
3. `curl -i -X POST http://127.0.0.1:8765/scan/Nonexistent` → confirm `404`.
4. While a scan is running (from step 2, or a physical button press), a second `curl -i -X POST http://127.0.0.1:8765/scan/Default` from another terminal → confirm `409`.
5. `curl -i -X OPTIONS http://127.0.0.1:8765/scan/Default` → confirm `204` with `Access-Control-Allow-Origin: *`.

---

### Task 4: Documentation — port/env var, curl smoke test, Dossiary profile setup

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: nothing new — this task only documents Tasks 1-3's finished behavior.

- [ ] **Step 1: Add a "Phase 4: HTTP bridge" section to README.md**

Insert after the existing "Phase 3: Physical Scan-button trigger" section (which ends right before the file's final content), following the same structure as Phases 1-3's own sections. This block uses a 4-backtick fence since its own content includes a nested ```json fence — use 4 backticks (not 3) when actually inserting it into README.md, or the inner fence will prematurely close the outer one:

````markdown
## Phase 4: HTTP bridge

No install step beyond what Phase 2 already needs — the bridge is built
into the menu bar app (`scanix500-menubar`) and requires no extra
dependency (`http.server` is part of the Python standard library).

### Use

With the menu bar app running, `POST /scan/<profile-name>` (the profile
name is the final URL path segment, percent-encoded) to
`http://127.0.0.1:8765` triggers a scan using that profile, exactly as if
you'd clicked it in the menu — and blocks until the scan finishes,
returning a JSON body:

```json
{"ok": true, "partial": false, "message": "/path/to/scan.pdf", "output_paths": ["/path/to/scan.pdf"]}
```

`ok: false` with `partial: true` means a partial scan (e.g. a multi-feed
jam) still produced a usable file, named in `output_paths`. `ok: false`
with `partial: false` means the scan failed outright. A `404` means no
profile by that name exists; a `409` means a scan is already in progress
(from any trigger — menu, hardware button, or another bridge request) and
this request was rejected immediately, not queued.

The port defaults to `8765`; override it by setting `SCANIX500_BRIDGE_PORT`
before launching `scanix500-menubar`.

**Smoke test** (with a profile named `"Default"` already configured and
paper loaded in the ADF):

    curl -i -X POST http://127.0.0.1:8765/scan/Default

**Trust model**: the bridge binds to `127.0.0.1` only and has no
authentication — the same trust boundary as the existing menu-click and
physical-button triggers (anyone with access to this machine can already
trigger a scan either way). Don't run this on a shared or networked
machine without understanding that any local process can hit this
endpoint.

### Dossiary integration

[Dossiary](https://github.com/AarneAarebye/Dossiary) (a separate app) has its
own "Scan"/"Scan Multi" toolbar buttons that call this bridge. They expect
two specific, fixed profile names to already exist:

- **`Dossiary Scan`** — an ordinary profile.
- **`Dossiary Scan Multi`** — a profile with "Split on blank separator
  sheets?" answered Yes.

Create both once via **Add Profile…** in the menu bar app, with their
destination set to your Dossiary library's actual `inbox/` folder (e.g.
`/path/to/your/library/inbox`) — Dossiary's own "Check inbox" flow picks
up whatever lands there. These names are a fixed contract Dossiary's own
code depends on; renaming either profile breaks the integration until
Dossiary's own setting is updated to match, or the profile is renamed
back.
````

Add these two lines to the existing "Manual UI checklist" list under Phase 2 (the list starting at the line beginning `- [x] **Fixed 2026-09-13, hardware-verified:** the app crashed...`):

```markdown
- [ ] The four `curl` checks in Task 3 Step 6 of
      `docs/superpowers/plans/2026-09-14-scanix500-http-bridge.md` all
      behave as described against the real running app.
```

- [ ] **Step 2: Add a CLAUDE.md architecture note**

Append a new bullet to CLAUDE.md's "Key constraints" section (or a new
top-level section if CLAUDE.md's structure has grown since this plan was
written — check its current shape first):

```markdown
- **The HTTP bridge (`src/scanix500/menubar/bridge.py`) is embedded in
  `scanix500-menubar`, not a standalone process** — it shares the app's
  existing `self._scanning` busy-guard and `_on_scan_complete()`
  notification path via `ScanixMenuBarApp.trigger()`
  (`ScanTrigger` protocol), so a scan triggered over HTTP is
  indistinguishable, from the app's own perspective, from a menu click or
  a physical button press. `bridge.py` itself has zero rumps/AppKit
  dependency and is fully unit-testable (`tests/menubar/test_bridge.py`);
  only the small `ScanixMenuBarApp.trigger()` integration in `app.py` is
  thread-marshaling/AppKit-dependent code, following this repo's existing
  "hardware/live-GUI code is manually verified, not mocked" precedent.
  Fixed, two-way contract with Dossiary: profiles named exactly
  `"Dossiary Scan"`/`"Dossiary Scan Multi"` must exist for its toolbar
  buttons to work — see README.md's own "Dossiary integration" section.
```

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "Document the HTTP bridge: port/env var, curl smoke test, Dossiary profile contract"
```
