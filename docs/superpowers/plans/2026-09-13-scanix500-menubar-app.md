# scanix500 Menu Bar App (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a macOS menu bar app that runs the existing `scanix500` CLI via profiles (saved destination + flag combinations), showing scan progress and results without needing a terminal.

**Architecture:** A pure-logic `profiles.py` module manages a JSON-backed list of named scan profiles. A pure-logic `runner.py` module invokes `scanix500` as a subprocess for a given profile and parses its stdout/stderr/exit code into a structured result. A `rumps`-based `app.py` wires these into a menu bar UI: one click per profile to scan, sequential-dialog flows to add/edit/delete profiles, an icon state change while scanning, and a macOS notification on completion (success, partial success on multi-feed, or failure).

**Tech Stack:** Python 3.11+, `rumps` (macOS menu bar framework, pulls in PyObjC), `pytest`.

## Global Constraints

- The menu bar app invokes `scanix500` as a subprocess only — it never imports `capture.py`, `pdf_builder.py`, `blank_filter.py`, or `router.py` directly. The CLI remains the single tested implementation of the scanning pipeline.
- Profiles are stored as JSON at `~/Library/Application Support/scanix500/profiles.json`.
- Only one scan runs at a time; a click on any profile (or an Add/Edit/Delete action) while a scan is in flight is a no-op guarded by an in-memory `_scanning` flag — no menu items need to be visually disabled for this to be safe.
- A multi-feed jam that still produced a partial PDF must be reported to the user as **partial success** (distinct wording/framing), not folded into a bare "failed" message — this is a headline behavior of the underlying CLI's `MultiFeedError` handling and must be visible in the notification.
- `profiles.py` and `runner.py` are pure logic (no `rumps`/AppKit imports) and get full automated test coverage. `app.py` (the actual `rumps.App` — menu construction, dialogs, icon, notifications) is **not** unit tested — per the existing project convention (see Phase 1's `PySaneDevice`), GUI/hardware-adjacent code that can't be meaningfully tested without a live session is verified via a manual checklist instead. Do not attempt to mock `rumps`/AppKit to unit-test `app.py`.
- Destination-directory creation is already handled by the existing `scanix500` CLI (`cli.py`'s `main()` calls `args.destination.mkdir(parents=True, exist_ok=True)`) — `profiles.py` and `app.py` must not duplicate that responsibility.
- Some exact API behaviors of `rumps`/AppKit (e.g. `rumps.alert`'s return-value convention, `NSOpenPanel.runModal()`'s success return code, and whether `self.menu.clear()`/`self.menu.add(...)` is the correct way to rebuild a `rumps.App`'s menu at runtime vs. reassigning `self.menu = [...]`) cannot be verified in this development environment (no interactive macOS GUI session available here). Code them per their documented behavior and flag them for verification in the manual checklist — this mirrors how Phase 1 handled `PySaneDevice`'s unverifiable SANE specifics. If `_rebuild_menu`'s `clear()`/`add()` calls turn out to be wrong against the installed rumps version, the fix is confined to that one method — no other code depends on the mutation mechanism.

---

### Task 1: Profile storage and management (`profiles.py`)

**Files:**
- Create: `src/scanix500/menubar/__init__.py` (empty)
- Create: `src/scanix500/menubar/profiles.py`
- Test: `tests/menubar/__init__.py` (empty)
- Test: `tests/menubar/test_profiles.py`

**Interfaces:**
- Produces: `scanix500.menubar.profiles.Profile` (dataclass: `name: str`, `destination: str`, `skip_blank_filter: bool = False`, `skip_ocr: bool = False`, `split_on_blank: bool = False`), `default_profiles_path() -> Path`, `load_profiles(path: Path) -> list[Profile]`, `save_profiles(path: Path, profiles: list[Profile]) -> None`, `add_profile(profiles: list[Profile], profile: Profile) -> list[Profile]`, `replace_profile(profiles: list[Profile], name: str, updated: Profile) -> list[Profile]` (raises `ValueError` if `name` not found), `delete_profile(profiles: list[Profile], name: str) -> list[Profile]` (raises `ValueError` if `name` not found, or if it's the last remaining profile) — all used by Task 3 (`app.py`).

- [ ] **Step 1: Write the failing test for save/load round-trip**

```python
# tests/menubar/test_profiles.py
from pathlib import Path

from scanix500.menubar.profiles import Profile, load_profiles, save_profiles


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "profiles.json"
    profiles = [
        Profile(name="Documents", destination="/tmp/docs", skip_ocr=False),
        Profile(name="Receipts", destination="/tmp/receipts", skip_ocr=True, split_on_blank=True),
    ]

    save_profiles(path, profiles)
    loaded = load_profiles(path)

    assert loaded == profiles
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/menubar/test_profiles.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scanix500.menubar'`

- [ ] **Step 3: Create the package skeleton and implement Profile, save_profiles, and load_profiles (existing-file case)**

Create empty `src/scanix500/menubar/__init__.py` and empty `tests/menubar/__init__.py`.

```python
# src/scanix500/menubar/profiles.py
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Profile:
    name: str
    destination: str
    skip_blank_filter: bool = False
    skip_ocr: bool = False
    split_on_blank: bool = False


def default_profiles_path() -> Path:
    return Path.home() / "Library" / "Application Support" / "scanix500" / "profiles.json"


def load_profiles(path: Path) -> list[Profile]:
    data = json.loads(path.read_text())
    return [Profile(**entry) for entry in data]


def save_profiles(path: Path, profiles: list[Profile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(p) for p in profiles], indent=2))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/menubar/test_profiles.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/menubar/__init__.py src/scanix500/menubar/profiles.py tests/menubar/__init__.py tests/menubar/test_profiles.py
git commit -m "Add Profile dataclass with JSON save/load round-trip"
```

- [ ] **Step 6: Write the failing test for seeding a default profile when the file is missing**

```python
# add to tests/menubar/test_profiles.py
def test_load_profiles_seeds_default_when_file_missing(tmp_path):
    path = tmp_path / "does-not-exist" / "profiles.json"

    loaded = load_profiles(path)

    assert len(loaded) == 1
    assert loaded[0].name == "Default"
    assert path.exists()  # the seeded default was persisted
```

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest tests/menubar/test_profiles.py -v`
Expected: FAIL with `FileNotFoundError` (from `path.read_text()` on a missing file)

- [ ] **Step 8: Implement the seeding branch**

```python
# replace load_profiles in src/scanix500/menubar/profiles.py
def _default_profile() -> Profile:
    return Profile(
        name="Default",
        destination=str(Path.home() / "Documents" / "Scans"),
    )


def load_profiles(path: Path) -> list[Profile]:
    if not path.exists():
        seeded = [_default_profile()]
        save_profiles(path, seeded)
        return seeded
    data = json.loads(path.read_text())
    return [Profile(**entry) for entry in data]
```

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest tests/menubar/test_profiles.py -v`
Expected: PASS (2 tests)

- [ ] **Step 10: Commit**

```bash
git add src/scanix500/menubar/profiles.py tests/menubar/test_profiles.py
git commit -m "Seed a default profile when profiles.json is missing"
```

- [ ] **Step 11: Write the failing test for add_profile**

```python
# add to tests/menubar/test_profiles.py
from scanix500.menubar.profiles import add_profile


def test_add_profile_appends_to_the_list():
    existing = [Profile(name="Documents", destination="/tmp/docs")]
    new_profile = Profile(name="Receipts", destination="/tmp/receipts")

    result = add_profile(existing, new_profile)

    assert result == [existing[0], new_profile]
    assert existing == [Profile(name="Documents", destination="/tmp/docs")]  # original untouched
```

- [ ] **Step 12: Run test to verify it fails**

Run: `pytest tests/menubar/test_profiles.py -v`
Expected: FAIL with `ImportError: cannot import name 'add_profile'`

- [ ] **Step 13: Implement add_profile**

```python
# add to src/scanix500/menubar/profiles.py
def add_profile(profiles: list[Profile], profile: Profile) -> list[Profile]:
    return [*profiles, profile]
```

- [ ] **Step 14: Run test to verify it passes**

Run: `pytest tests/menubar/test_profiles.py -v`
Expected: PASS (3 tests)

- [ ] **Step 15: Commit**

```bash
git add src/scanix500/menubar/profiles.py tests/menubar/test_profiles.py
git commit -m "Add add_profile"
```

- [ ] **Step 16: Write the failing test for replace_profile (found case)**

```python
# add to tests/menubar/test_profiles.py
from scanix500.menubar.profiles import replace_profile


def test_replace_profile_updates_the_matching_entry_by_name():
    existing = [
        Profile(name="Documents", destination="/tmp/docs"),
        Profile(name="Receipts", destination="/tmp/receipts"),
    ]
    updated = Profile(name="Documents", destination="/tmp/new-docs", skip_ocr=True)

    result = replace_profile(existing, "Documents", updated)

    assert result == [updated, existing[1]]
```

- [ ] **Step 17: Run test to verify it fails**

Run: `pytest tests/menubar/test_profiles.py -v`
Expected: FAIL with `ImportError: cannot import name 'replace_profile'`

- [ ] **Step 18: Implement replace_profile, including its not-found branch**

```python
# add to src/scanix500/menubar/profiles.py
def replace_profile(profiles: list[Profile], name: str, updated: Profile) -> list[Profile]:
    if not any(p.name == name for p in profiles):
        raise ValueError(f"No profile named {name!r}")
    return [updated if p.name == name else p for p in profiles]
```

- [ ] **Step 19: Run test to verify it passes**

Run: `pytest tests/menubar/test_profiles.py -v`
Expected: PASS (4 tests)

- [ ] **Step 20: Commit**

```bash
git add src/scanix500/menubar/profiles.py tests/menubar/test_profiles.py
git commit -m "Add replace_profile"
```

- [ ] **Step 21: Write the failing test for replace_profile's not-found error**

```python
# add to tests/menubar/test_profiles.py
import pytest


def test_replace_profile_raises_for_unknown_name():
    existing = [Profile(name="Documents", destination="/tmp/docs")]

    with pytest.raises(ValueError):
        replace_profile(existing, "NoSuchProfile", Profile(name="X", destination="/tmp/x"))
```

- [ ] **Step 22: Run test to verify it passes**

Run: `pytest tests/menubar/test_profiles.py -v`

Step 18's implementation already raises `ValueError` for an unknown name, so this is expected to PASS immediately — confirming that branch rather than requiring new code.

Expected: PASS (5 tests)

- [ ] **Step 23: Commit**

```bash
git add tests/menubar/test_profiles.py
git commit -m "Add replace_profile not-found test coverage"
```

- [ ] **Step 24: Write the failing test for delete_profile (found, multiple remain)**

```python
# add to tests/menubar/test_profiles.py
from scanix500.menubar.profiles import delete_profile


def test_delete_profile_removes_the_matching_entry_by_name():
    existing = [
        Profile(name="Documents", destination="/tmp/docs"),
        Profile(name="Receipts", destination="/tmp/receipts"),
    ]

    result = delete_profile(existing, "Documents")

    assert result == [existing[1]]
```

- [ ] **Step 25: Run test to verify it fails**

Run: `pytest tests/menubar/test_profiles.py -v`
Expected: FAIL with `ImportError: cannot import name 'delete_profile'`

- [ ] **Step 26: Implement delete_profile, including both its error branches**

```python
# add to src/scanix500/menubar/profiles.py
def delete_profile(profiles: list[Profile], name: str) -> list[Profile]:
    if not any(p.name == name for p in profiles):
        raise ValueError(f"No profile named {name!r}")
    if len(profiles) == 1:
        raise ValueError("Cannot delete the last remaining profile")
    return [p for p in profiles if p.name != name]
```

- [ ] **Step 27: Run test to verify it passes**

Run: `pytest tests/menubar/test_profiles.py -v`
Expected: PASS (6 tests)

- [ ] **Step 28: Commit**

```bash
git add src/scanix500/menubar/profiles.py tests/menubar/test_profiles.py
git commit -m "Add delete_profile"
```

- [ ] **Step 29: Write the failing tests for delete_profile's two error branches**

```python
# add to tests/menubar/test_profiles.py
def test_delete_profile_raises_for_unknown_name():
    existing = [Profile(name="Documents", destination="/tmp/docs")]

    with pytest.raises(ValueError):
        delete_profile(existing, "NoSuchProfile")


def test_delete_profile_raises_when_only_one_profile_remains():
    existing = [Profile(name="Documents", destination="/tmp/docs")]

    with pytest.raises(ValueError):
        delete_profile(existing, "Documents")
```

- [ ] **Step 30: Run tests to verify they pass**

Run: `pytest tests/menubar/test_profiles.py -v`

Step 26's implementation already raises `ValueError` for both cases, so these are expected to PASS immediately — confirming those branches rather than requiring new code.

Expected: PASS (8 tests)

- [ ] **Step 31: Commit**

```bash
git add tests/menubar/test_profiles.py
git commit -m "Add delete_profile error-branch test coverage"
```

---

### Task 2: Scan execution and result parsing (`runner.py`)

**Files:**
- Create: `src/scanix500/menubar/runner.py`
- Test: `tests/menubar/test_runner.py`

**Interfaces:**
- Consumes: `scanix500.menubar.profiles.Profile` (Task 1).
- Produces: `scanix500.menubar.runner.ScanResult` (dataclass: `ok: bool`, `partial: bool`, `message: str`, `output_paths: list[str]`), `scanix500.menubar.runner.parse_scan_output(returncode: int, stdout: str, stderr: str) -> ScanResult`, `scanix500.menubar.runner.run_scan(profile: Profile) -> ScanResult` — used by Task 3 (`app.py`).

- [ ] **Step 1: Write the failing test for a successful scan with output paths**

```python
# tests/menubar/test_runner.py
from scanix500.menubar.runner import ScanResult, parse_scan_output


def test_parse_scan_output_success_with_paths():
    result = parse_scan_output(
        returncode=0,
        stdout="/tmp/scans/scan_2026-09-13_143012.pdf\n",
        stderr="",
    )

    assert result == ScanResult(
        ok=True,
        partial=False,
        message="Scan complete",
        output_paths=["/tmp/scans/scan_2026-09-13_143012.pdf"],
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/menubar/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scanix500.menubar.runner'`

- [ ] **Step 3: Implement ScanResult and parse_scan_output**

```python
# src/scanix500/menubar/runner.py
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from scanix500.menubar.profiles import Profile

_KNOWN_MESSAGES = {"No pages scanned.", "All pages were blank; no PDF written."}


@dataclass
class ScanResult:
    ok: bool
    partial: bool
    message: str
    output_paths: list[str]


def parse_scan_output(returncode: int, stdout: str, stderr: str) -> ScanResult:
    stdout_lines = [line for line in stdout.splitlines() if line]
    message_line = stdout_lines[0] if stdout_lines and stdout_lines[0] in _KNOWN_MESSAGES else None
    output_paths = [] if message_line else stdout_lines

    if returncode == 0:
        return ScanResult(
            ok=True,
            partial=False,
            message=message_line or "Scan complete",
            output_paths=output_paths,
        )

    stderr_lines = [line for line in stderr.splitlines() if line]
    multi_feed = any("Multi-feed detected" in line for line in stderr_lines)

    if multi_feed:
        stderr_message = stderr_lines[0] if stderr_lines else "Multi-feed detected"
        if output_paths:
            message = f"{stderr_message} — partial scan saved to {output_paths[0]}"
        elif message_line:
            message = f"{stderr_message} ({message_line})"
        else:
            message = stderr_message
        return ScanResult(ok=False, partial=True, message=message, output_paths=output_paths)

    message = stderr_lines[0] if stderr_lines else f"scanix500 exited with code {returncode}"
    return ScanResult(ok=False, partial=False, message=message, output_paths=[])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/menubar/test_runner.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/menubar/runner.py tests/menubar/test_runner.py
git commit -m "Add ScanResult and parse_scan_output success path"
```

- [ ] **Step 6: Write the failing tests for the remaining parse_scan_output cases**

```python
# add to tests/menubar/test_runner.py
def test_parse_scan_output_no_pages_scanned():
    result = parse_scan_output(returncode=0, stdout="No pages scanned.\n", stderr="")

    assert result == ScanResult(ok=True, partial=False, message="No pages scanned.", output_paths=[])


def test_parse_scan_output_all_blank():
    result = parse_scan_output(
        returncode=0, stdout="All pages were blank; no PDF written.\n", stderr=""
    )

    assert result == ScanResult(
        ok=True,
        partial=False,
        message="All pages were blank; no PDF written.",
        output_paths=[],
    )


def test_parse_scan_output_multi_feed_with_partial_pdf():
    result = parse_scan_output(
        returncode=1,
        stdout="/tmp/scans/scan_2026-09-13_143012.pdf\n",
        stderr="Multi-feed detected at sheet 3; processing 2 pages captured before the jam.\n",
    )

    assert result.ok is False
    assert result.partial is True
    assert result.output_paths == ["/tmp/scans/scan_2026-09-13_143012.pdf"]
    assert "Multi-feed detected at sheet 3" in result.message
    assert "/tmp/scans/scan_2026-09-13_143012.pdf" in result.message


def test_parse_scan_output_multi_feed_with_all_blank_partial_batch():
    result = parse_scan_output(
        returncode=1,
        stdout="All pages were blank; no PDF written.\n",
        stderr="Multi-feed detected at sheet 1; processing 1 pages captured before the jam.\n",
    )

    assert result.ok is False
    assert result.partial is True
    assert result.output_paths == []
    assert "Multi-feed detected at sheet 1" in result.message
    assert "All pages were blank" in result.message


def test_parse_scan_output_generic_failure():
    result = parse_scan_output(
        returncode=1,
        stdout="",
        stderr="Scanner not found: No iX500 found via SANE fujitsu backend\n",
    )

    assert result == ScanResult(
        ok=False,
        partial=False,
        message="Scanner not found: No iX500 found via SANE fujitsu backend",
        output_paths=[],
    )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/menubar/test_runner.py -v`

Step 3's implementation already handles all of these branches, so these are expected to PASS immediately — confirming that behavior rather than requiring new code.

Expected: PASS (6 tests)

- [ ] **Step 8: Commit**

```bash
git add tests/menubar/test_runner.py
git commit -m "Add parse_scan_output coverage for no-pages, all-blank, multi-feed, and generic-failure cases"
```

- [ ] **Step 9: Write the failing test for run_scan**

```python
# add to tests/menubar/test_runner.py
from unittest.mock import MagicMock, patch

from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import run_scan


def test_run_scan_builds_argv_from_profile_flags_and_delegates_to_parse():
    profile = Profile(
        name="Receipts",
        destination="/tmp/receipts",
        skip_blank_filter=True,
        skip_ocr=True,
        split_on_blank=True,
    )
    fake_completed = MagicMock(returncode=0, stdout="/tmp/receipts/scan_1.pdf\n", stderr="")

    with patch("scanix500.menubar.runner.subprocess.run", return_value=fake_completed) as mock_run:
        result = run_scan(profile)

    mock_run.assert_called_once_with(
        [
            "scanix500",
            "/tmp/receipts",
            "--skip-blank-filter",
            "--skip-ocr",
            "--split-on-blank",
        ],
        capture_output=True,
        text=True,
    )
    assert result.ok is True
    assert result.output_paths == ["/tmp/receipts/scan_1.pdf"]
```

- [ ] **Step 10: Run test to verify it fails**

Run: `pytest tests/menubar/test_runner.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_scan'`

- [ ] **Step 11: Implement run_scan and its argv-building helper**

```python
# add to src/scanix500/menubar/runner.py
def _build_argv(profile: Profile) -> list[str]:
    argv = ["scanix500", profile.destination]
    if profile.skip_blank_filter:
        argv.append("--skip-blank-filter")
    if profile.skip_ocr:
        argv.append("--skip-ocr")
    if profile.split_on_blank:
        argv.append("--split-on-blank")
    return argv


def run_scan(profile: Profile) -> ScanResult:
    result = subprocess.run(_build_argv(profile), capture_output=True, text=True)
    return parse_scan_output(result.returncode, result.stdout, result.stderr)
```

- [ ] **Step 12: Run test to verify it passes**

Run: `pytest tests/menubar/test_runner.py -v`
Expected: PASS (7 tests)

- [ ] **Step 13: Commit**

```bash
git add src/scanix500/menubar/runner.py tests/menubar/test_runner.py
git commit -m "Add run_scan wiring profile flags to a scanix500 subprocess call"
```

---

### Task 3: Menu bar app (`app.py`)

**Files:**
- Create: `src/scanix500/menubar/app.py`

**Interfaces:**
- Consumes: everything from Task 1 (`Profile`, `default_profiles_path`, `load_profiles`, `save_profiles`, `add_profile`, `replace_profile`, `delete_profile`) and Task 2 (`run_scan`, `ScanResult`).
- Produces: `scanix500.menubar.app.main()` — the `scanix500-menubar` console-script entry point (registered in Task 4's `pyproject.toml`).

This file is GUI code (`rumps`/AppKit) with no automated test coverage, per the Global Constraints above — there is no TDD cycle for this task. Implement it in one pass, sanity-check that it compiles, and commit. Full verification happens later via the manual checklist (Task 4).

- [ ] **Step 1: Implement the menu bar app**

```python
# src/scanix500/menubar/app.py
from __future__ import annotations

import threading
from pathlib import Path

import rumps
from AppKit import NSOpenPanel
from Foundation import NSURL

from scanix500.menubar.profiles import (
    Profile,
    add_profile,
    default_profiles_path,
    delete_profile,
    load_profiles,
    replace_profile,
    save_profiles,
)
from scanix500.menubar.runner import ScanResult, run_scan

IDLE_TITLE = "📄"
SCANNING_TITLE = "📄…"


def _pick_folder(default_path: str) -> str | None:
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseDirectories_(True)
    panel.setCanChooseFiles_(False)
    panel.setCanCreateDirectories_(True)
    panel.setAllowsMultipleSelection_(False)
    panel.setDirectoryURL_(NSURL.fileURLWithPath_(default_path))
    # NOTE: 1 is NSOpenPanel's documented "OK button" response. Verify this
    # against the installed AppKit/PyObjC version in the manual checklist —
    # it cannot be exercised without a live GUI session.
    if panel.runModal() == 1:
        return str(panel.URL().path())
    return None


class ScanixMenuBarApp(rumps.App):
    def __init__(self):
        super().__init__(IDLE_TITLE)
        self.profiles_path = default_profiles_path()
        self.profiles = load_profiles(self.profiles_path)
        self._scanning = False
        self._rebuild_menu()

    def _rebuild_menu(self):
        self.menu.clear()
        for profile in self.profiles:
            self.menu.add(rumps.MenuItem(profile.name, callback=self._make_scan_handler(profile)))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Add Profile…", callback=self._add_profile))

        edit_menu = rumps.MenuItem("Edit Profile…")
        for profile in self.profiles:
            edit_menu.add(rumps.MenuItem(profile.name, callback=self._make_edit_handler(profile.name)))
        self.menu.add(edit_menu)

        delete_menu = rumps.MenuItem("Delete Profile…")
        for profile in self.profiles:
            delete_menu.add(rumps.MenuItem(profile.name, callback=self._make_delete_handler(profile.name)))
        self.menu.add(delete_menu)

    def _make_scan_handler(self, profile: Profile):
        def handler(_sender):
            if self._scanning:
                return
            self._scanning = True
            self.title = SCANNING_TITLE
            threading.Thread(target=self._run_scan_thread, args=(profile,), daemon=True).start()
        return handler

    def _run_scan_thread(self, profile: Profile):
        result = run_scan(profile)

        def deliver(timer):
            timer.stop()
            self._on_scan_complete(result)

        rumps.Timer(deliver, 0.1).start()

    def _on_scan_complete(self, result: ScanResult):
        self._scanning = False
        self.title = IDLE_TITLE
        if result.ok and not result.partial:
            title = "Scan complete"
        elif result.partial:
            title = "Scan partially completed"
        else:
            title = "Scan failed"
        rumps.notification(title=title, subtitle="", message=result.message)

    def _prompt_profile_fields(self, existing: Profile | None) -> Profile | None:
        name_response = rumps.Window(
            "Profile name:", "Profile", default_text=existing.name if existing else ""
        ).run()
        if not name_response.clicked or not name_response.text:
            return None
        name = name_response.text

        default_destination = existing.destination if existing else str(Path.home() / "Documents" / "Scans")
        destination = _pick_folder(default_destination)
        if destination is None:
            return None

        # NOTE: rumps.alert's return value convention (1 = the `ok` button,
        # 0 = the `cancel` button) is documented but unverified in this
        # environment — confirm in the manual checklist.
        skip_blank_filter = rumps.alert("Profile", "Skip blank-page filter?", ok="Yes", cancel="No") == 1
        skip_ocr = rumps.alert("Profile", "Skip OCR?", ok="Yes", cancel="No") == 1
        split_on_blank = rumps.alert(
            "Profile", "Split on blank separator sheets?", ok="Yes", cancel="No"
        ) == 1

        return Profile(name, destination, skip_blank_filter, skip_ocr, split_on_blank)

    def _add_profile(self, _sender):
        if self._scanning:
            return
        profile = self._prompt_profile_fields(None)
        if profile is None:
            return
        self.profiles = add_profile(self.profiles, profile)
        save_profiles(self.profiles_path, self.profiles)
        self._rebuild_menu()

    def _make_edit_handler(self, name: str):
        def handler(_sender):
            if self._scanning:
                return
            current = next(p for p in self.profiles if p.name == name)
            updated = self._prompt_profile_fields(current)
            if updated is None:
                return
            self.profiles = replace_profile(self.profiles, name, updated)
            save_profiles(self.profiles_path, self.profiles)
            self._rebuild_menu()
        return handler

    def _make_delete_handler(self, name: str):
        def handler(_sender):
            if self._scanning:
                return
            confirmed = rumps.alert("Delete Profile", f'Delete profile "{name}"?', ok="Delete", cancel="Cancel") == 1
            if not confirmed:
                return
            try:
                self.profiles = delete_profile(self.profiles, name)
            except ValueError as e:
                rumps.alert("Delete Profile", str(e))
                return
            save_profiles(self.profiles_path, self.profiles)
            self._rebuild_menu()
        return handler


def main():
    ScanixMenuBarApp().run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Sanity-check the module imports cleanly**

Run: `pip install -e ".[menubar,dev]"` (installs `rumps`, which pulls in PyObjC), then:
`python -c "import scanix500.menubar.app"`
Expected: no output, exit code 0. This confirms the syntax is valid and every import (`rumps`, `AppKit`, `Foundation`, and the Task 1/2 modules) actually resolves — it does not exercise the GUI itself, since creating/running a live `rumps.App` requires an interactive session, but it does catch import-time errors (missing dependency, typo'd module path) before they'd otherwise only surface when a person tries to launch the app.

- [ ] **Step 3: Run the full existing test suite to confirm nothing else broke**

Run: `pytest -v`
Expected: all previously-passing tests still pass (this task adds no new automated tests)

- [ ] **Step 4: Commit**

```bash
git add src/scanix500/menubar/app.py
git commit -m "Add rumps-based menu bar app wiring profiles and scan execution"
```

---

### Task 4: Packaging, LaunchAgent, and documentation

**Files:**
- Modify: `pyproject.toml`
- Create: `packaging/com.scanix500.menubar.plist`
- Modify: `README.md`

- [ ] **Step 1: Add the menubar optional dependency and console script**

```toml
# pyproject.toml — replace the [project.optional-dependencies] and [project.scripts] sections
[project.optional-dependencies]
dev = ["pytest>=8.0"]
menubar = ["rumps>=0.4"]

[project.scripts]
scanix500 = "scanix500.cli:main"
scanix500-menubar = "scanix500.menubar.app:main"
```

- [ ] **Step 2: Verify pyproject.toml is still valid and the package installs**

Run: `pip install -e ".[menubar,dev]"`
Expected: installs successfully, including `rumps` and its PyObjC dependencies

- [ ] **Step 3: Create the LaunchAgent plist template**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.scanix500.menubar</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/your/venv/bin/scanix500-menubar</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>/tmp/scanix500-menubar.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/scanix500-menubar.log</string>
</dict>
</plist>
```

- [ ] **Step 4: Update README.md with a Phase 2 section**

Add a new section after the existing "Manual hardware checklist" section:

```markdown
## Phase 2: Menu bar app

### Install

    pip install -e ".[menubar]"

### Run manually

    scanix500-menubar

A menu bar icon (📄) appears with one item per saved profile, plus
Add/Edit/Delete Profile submenus. Profiles are stored at
`~/Library/Application Support/scanix500/profiles.json` and seeded with a
single "Default" profile on first run.

### Auto-launch at login

1. Find the installed script's full path: `which scanix500-menubar`
2. Copy `packaging/com.scanix500.menubar.plist` to `~/Library/LaunchAgents/`
3. Edit the copied plist's `ProgramArguments` entry to the path from step 1
4. `launchctl load ~/Library/LaunchAgents/com.scanix500.menubar.plist`

To stop it from auto-launching: `launchctl unload ~/Library/LaunchAgents/com.scanix500.menubar.plist`
then remove the plist file.

### Manual UI checklist

Run these by hand — `app.py` has no automated tests, since it's live
GUI/AppKit code:

- [ ] Clicking a profile in the menu triggers a scan; the icon changes
      to the "scanning" state and reverts when done.
- [ ] A successful scan shows a "Scan complete" notification naming the
      output path.
- [ ] An all-blank batch shows a notification with that message, no crash.
- [ ] A scanner-not-found or other hard failure shows a "Scan failed"
      notification with a clear message.
- [ ] A multi-feed jam (see Phase 1's manual hardware checklist) shows a
      "Scan partially completed" notification distinct from a bare failure,
      naming the partial output path.
- [ ] Add Profile: name entry → folder picker → three yes/no prompts →
      new profile appears in the menu and in `profiles.json`.
- [ ] Edit Profile: existing values are used as the starting point in the
      name/destination prompts; changes are saved and reflected in the menu.
- [ ] Delete Profile: confirmation prompt appears; deleting the last
      remaining profile is refused with a clear alert instead of silently
      failing or leaving an empty menu.
- [ ] Clicking a profile, or Add/Edit/Delete, while a scan is already in
      progress does nothing (no double-scan, no crash).
- [ ] Quit and relaunch `scanix500-menubar`: profiles persist correctly
      from `profiles.json`.
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml packaging/com.scanix500.menubar.plist README.md
git commit -m "Add menubar packaging, LaunchAgent template, and Phase 2 docs"
```
