# scanix500 Physical Scan-Button Trigger (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pressing the iX500's physical Scan button triggers a scan through the existing menu bar app, using a dedicated auto-seeded "Hardware Button" profile.

**Architecture:** A `rumps.Timer` polls the SANE `scan` sensor option every 1.5s, opening/checking/closing the device each cycle (confirmed ~5-20ms, cheap enough not to monopolize the scanner). On a press, it reuses the exact same scan-launch machinery already used by profile-menu clicks. A shared `find_fujitsu_device()` helper (extracted from `PySaneDevice`) is used by both the existing capture pipeline and the new button watcher.

**Tech Stack:** Python 3.11+, `python-sane` (already a manual/optional dependency, matching Phase 1's `PySaneDevice`), `rumps` (already a dependency from Phase 2), `pytest`.

## Global Constraints

- No external daemon (`scanbd`/`insaned`) — button watching lives inside the existing menu bar app.
- Poll interval is 1.5 seconds.
- The device is opened and closed each poll cycle, never held open continuously — confirmed cheap (~5-20ms per cycle) against real hardware, and necessary so other applications (or `scanix500` itself mid-scan) can still use the scanner between polls.
- Option descriptors must be re-fetched (`dev.get_options()`) immediately before reading the `scan` option's raw value each cycle — confirmed empirically that a stale/cached descriptor set does not reflect a fresh button press.
- The `scan` SANE option's value must be read via `dev.get_options()` (re-fetched each cycle) to find the `scan` option's index, then `dev.dev.get_option(index)` for its raw value — never `dev.scan`, since `SaneDev.scan` is `python-sane`'s own bound method (for triggering a scan) and shadows the SANE option of the same name.
- Any failure in the button-watching path (device busy, unplugged, transient error) must be treated as "not pressed" and never crash the app or spam notifications — the poller tries again next cycle.
- Hardware-facing code (`button_watcher.py`) gets NO automated tests, matching the existing `PySaneDevice` precedent — it is verified via the manual checklist, not mocked.
- The "Hardware Button" profile is seeded/migrated in on every `load_profiles()` call, not only on a fresh install, so an existing installation (with an existing `profiles.json` lacking it) gets it automatically the next time the app starts.
- `find_fujitsu_device(devices) -> str | None` is shared between `PySaneDevice` (existing) and `button_watcher.py` (new) — one implementation, fully unit tested as a pure function.

---

### Task 1: Extract shared device-matching logic (`find_fujitsu_device`)

**Files:**
- Modify: `src/scanix500/capture.py`
- Test: `tests/test_capture.py`

**Interfaces:**
- Produces: `scanix500.capture.find_fujitsu_device(devices: list[tuple]) -> str | None` — takes the `(name, vendor, model, type)` tuples `sane.get_devices()` returns; returns the matched device's `name`, or `None`. Used by Task 3 (`button_watcher.py`) and internally by `PySaneDevice.__init__` (this task).

- [ ] **Step 1: Write the failing test for a vendor-string match**

```python
# add to tests/test_capture.py
from scanix500.capture import find_fujitsu_device


def test_find_fujitsu_device_matches_by_vendor():
    devices = [
        ("epson:net:1.2.3.4", "EPSON", "Perfection V600", "scanner"),
        ("fujitsu:ScanSnap iX500:1203900", "FUJITSU", "ScanSnap iX500", "scanner"),
    ]

    assert find_fujitsu_device(devices) == "fujitsu:ScanSnap iX500:1203900"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_capture.py -v`
Expected: FAIL with `ImportError: cannot import name 'find_fujitsu_device'`

- [ ] **Step 3: Implement find_fujitsu_device and refactor PySaneDevice to use it**

```python
# add to src/scanix500/capture.py, above the PySaneDevice class
def find_fujitsu_device(devices: list[tuple]) -> str | None:
    """Given the (name, vendor, model, type) tuples sane.get_devices()
    returns, finds the iX500 and returns its device name, or None if
    not found."""
    for d in devices:
        if "fujitsu" in d[1].lower() or "ix500" in d[2].lower():
            return d[0]
    return None
```

Then replace the inline matching block inside `PySaneDevice.__init__` (the lines starting `# get_devices() yields (name, vendor, model, type) tuples...` through the `sane.open(...)` call) with:

```python
        try:
            devices = sane.get_devices()
        except Exception as e:
            raise ScannerNotFoundError(f"Failed to enumerate SANE devices: {e}") from e

        device_name = find_fujitsu_device(devices)
        if device_name is None:
            raise ScannerNotFoundError("No iX500 found via SANE fujitsu backend")

        try:
            self._dev = sane.open(device_name)
        except Exception as e:
            raise ScannerNotFoundError(f"Failed to open SANE device: {e}") from e
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_capture.py -v`
Expected: PASS (6 tests: 5 existing + 1 new)

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/capture.py tests/test_capture.py
git commit -m "Extract find_fujitsu_device, shared device-matching helper"
```

- [ ] **Step 6: Write the failing tests for the model-substring match and no-match cases**

```python
# add to tests/test_capture.py
def test_find_fujitsu_device_matches_by_model_substring():
    # Vendor string doesn't say "fujitsu", but the model does say "ix500".
    devices = [("some:other:device", "ACME", "Widget iX500 Pro", "scanner")]

    assert find_fujitsu_device(devices) == "some:other:device"


def test_find_fujitsu_device_returns_none_when_not_found():
    devices = [("epson:net:1.2.3.4", "EPSON", "Perfection V600", "scanner")]

    assert find_fujitsu_device(devices) is None
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_capture.py -v`

Step 3's implementation already handles both branches (the `or` in the single-line condition), so these are expected to PASS immediately — confirming that behavior rather than requiring new code.

Expected: PASS (8 tests)

- [ ] **Step 8: Commit**

```bash
git add tests/test_capture.py
git commit -m "Add model-substring and no-match test coverage for find_fujitsu_device"
```

---

### Task 2: Auto-seed and migrate the "Hardware Button" profile

**Files:**
- Modify: `src/scanix500/menubar/profiles.py`
- Modify: `tests/menubar/test_profiles.py`

**Interfaces:**
- Produces: `scanix500.menubar.profiles.HARDWARE_BUTTON_PROFILE_NAME` (the string `"Hardware Button"`) — used by Task 4 (`app.py`) to find the profile the button should trigger.
- Modifies the behavior (not the signature) of `scanix500.menubar.profiles.load_profiles(path: Path) -> list[Profile]`, already consumed by `app.py` — it now also ensures a `"Hardware Button"` profile exists, migrating it in if a loaded file lacks one.

This task modifies three existing tests' expectations in addition to adding new coverage, because seeding now produces two profiles instead of one. Each modification is called out explicitly below — do not skip them, since the existing tests would otherwise fail against the new behavior.

- [ ] **Step 1: Write the failing test for migrating the profile into an existing valid file**

```python
# add to tests/menubar/test_profiles.py
from scanix500.menubar.profiles import HARDWARE_BUTTON_PROFILE_NAME


def test_load_profiles_adds_hardware_button_profile_when_missing_from_existing_file(tmp_path):
    path = tmp_path / "profiles.json"
    save_profiles(path, [Profile(name="Documents", destination="/tmp/docs")])

    loaded = load_profiles(path)

    assert [p.name for p in loaded] == ["Documents", HARDWARE_BUTTON_PROFILE_NAME]
    # The migration was persisted, not just returned in memory:
    assert [p.name for p in load_profiles(path)] == ["Documents", HARDWARE_BUTTON_PROFILE_NAME]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/menubar/test_profiles.py -v`
Expected: FAIL with `ImportError: cannot import name 'HARDWARE_BUTTON_PROFILE_NAME'`

- [ ] **Step 3: Implement the constant, helper, and migration branch — and update the one existing test this affects**

```python
# add to src/scanix500/menubar/profiles.py, near _default_profile
HARDWARE_BUTTON_PROFILE_NAME = "Hardware Button"


def _hardware_button_profile() -> Profile:
    return Profile(
        name=HARDWARE_BUTTON_PROFILE_NAME,
        destination=str(Path.home() / "Documents" / "Scans"),
    )
```

Replace `load_profiles` with:

```python
def load_profiles(path: Path) -> list[Profile]:
    if not path.exists():
        return _seed_default(path)
    try:
        data = json.loads(path.read_text())
        profiles = [Profile(**entry) for entry in data]
    except (json.JSONDecodeError, TypeError, KeyError):
        # A corrupt or schema-drifted profiles.json must not crash the app
        # before the menu bar icon ever appears (under launchd there is no
        # terminal to show the traceback). Recover with a fresh default.
        return _seed_default(path)

    if not any(p.name == HARDWARE_BUTTON_PROFILE_NAME for p in profiles):
        # Migrate existing installations (profiles.json predates Phase 3)
        # so the button works after an upgrade with no manual step.
        profiles = [*profiles, _hardware_button_profile()]
        save_profiles(path, profiles)
    return profiles
```

`_seed_default` is unchanged in this step — it still seeds only `"Default"`. That means `test_load_profiles_seeds_default_when_file_missing`, `test_load_profiles_falls_back_to_default_when_file_is_corrupt`, and `test_load_profiles_falls_back_to_default_on_unknown_field` are unaffected by this step and should still pass as-is.

`test_save_and_load_round_trip` IS affected: it saves a list without a `"Hardware Button"` profile, so the new migration branch would append one and break the round-trip assertion (`loaded == profiles`). Update its fixture data so the saved list already includes one, keeping the test's original intent (verifying save/load fidelity) intact:

```python
# replace test_save_and_load_round_trip in tests/menubar/test_profiles.py
def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "profiles.json"
    profiles = [
        Profile(name="Documents", destination="/tmp/docs", skip_ocr=False),
        Profile(name="Receipts", destination="/tmp/receipts", skip_ocr=True, split_on_blank=True),
        Profile(name=HARDWARE_BUTTON_PROFILE_NAME, destination="/tmp/docs"),
    ]

    save_profiles(path, profiles)
    loaded = load_profiles(path)

    assert loaded == profiles
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/menubar/test_profiles.py -v`
Expected: PASS (15 tests: 14 existing, one of which (`test_save_and_load_round_trip`) was just modified in place, plus 1 new)

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/menubar/profiles.py tests/menubar/test_profiles.py
git commit -m "Migrate a Hardware Button profile into existing profiles.json files"
```

- [ ] **Step 6: Write the failing tests for the fresh-install seeding case, updating the three existing tests it changes**

```python
# replace test_load_profiles_seeds_default_when_file_missing in tests/menubar/test_profiles.py
# (keep the same function name — this replaces the body in place)
def test_load_profiles_seeds_default_when_file_missing(tmp_path):
    path = tmp_path / "does-not-exist" / "profiles.json"

    loaded = load_profiles(path)

    assert [p.name for p in loaded] == ["Default", HARDWARE_BUTTON_PROFILE_NAME]
    assert path.exists()  # the seeded profiles were persisted
```

```python
# replace test_load_profiles_falls_back_to_default_when_file_is_corrupt
def test_load_profiles_falls_back_to_default_when_file_is_corrupt(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("{not valid json")

    loaded = load_profiles(path)

    assert [p.name for p in loaded] == ["Default", HARDWARE_BUTTON_PROFILE_NAME]
    assert [p.name for p in load_profiles(path)] == ["Default", HARDWARE_BUTTON_PROFILE_NAME]
```

```python
# replace test_load_profiles_falls_back_to_default_on_unknown_field
def test_load_profiles_falls_back_to_default_on_unknown_field(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text('[{"name": "Old", "destination": "/tmp/old", "removed_field": true}]')

    loaded = load_profiles(path)

    assert [p.name for p in loaded] == ["Default", HARDWARE_BUTTON_PROFILE_NAME]
```

- [ ] **Step 7: Run test to verify it fails**

Run: `.venv/bin/pytest tests/menubar/test_profiles.py -v`
Expected: FAIL (all three: `_seed_default` still only seeds `"Default"`, so `loaded` has 1 profile, not 2)

- [ ] **Step 8: Update `_seed_default` to seed both profiles**

```python
# replace _seed_default in src/scanix500/menubar/profiles.py
def _seed_default(path: Path) -> list[Profile]:
    seeded = [_default_profile(), _hardware_button_profile()]
    save_profiles(path, seeded)
    return seeded
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/menubar/test_profiles.py -v`
Expected: PASS (15 tests — same count as Step 4, since Steps 6-8 replaced three existing tests' bodies in place rather than adding new ones)

- [ ] **Step 10: Run the full test suite to confirm nothing else broke**

Run: `.venv/bin/pytest -v`
Expected: PASS (57 tests: 56 after Task 1, plus the one net-new test this task added in Step 1)

- [ ] **Step 11: Commit**

```bash
git add src/scanix500/menubar/profiles.py tests/menubar/test_profiles.py
git commit -m "Seed a Hardware Button profile alongside Default on fresh install"
```

---

### Task 3: Button-press detection (`button_watcher.py`)

**Files:**
- Create: `src/scanix500/menubar/button_watcher.py`

**Interfaces:**
- Consumes: `scanix500.capture.find_fujitsu_device` (Task 1).
- Produces: `scanix500.menubar.button_watcher.resolve_device_name() -> str | None`, `scanix500.menubar.button_watcher.button_pressed(device_name: str) -> bool` — both used by Task 4 (`app.py`).

This file is hardware-dependent code with NO automated tests, per the Global Constraints above — same category as `PySaneDevice`. There is no TDD cycle for this task. Implement it in one pass, verify it imports cleanly, and commit. Full verification happens via the manual checklist (Task 5).

- [ ] **Step 1: Implement button_watcher.py**

```python
# src/scanix500/menubar/button_watcher.py
from __future__ import annotations

from scanix500.capture import find_fujitsu_device


def resolve_device_name() -> str | None:
    """Finds the iX500's SANE device name, or None if it can't be found
    right now (not installed, not plugged in, transient failure). Safe to
    call repeatedly — never raises."""
    try:
        import sane
    except ImportError:
        return None
    try:
        sane.init()
        devices = sane.get_devices()
    except Exception:
        return None
    return find_fujitsu_device(devices)


def button_pressed(device_name: str) -> bool:
    """Opens the device, checks whether the physical Scan button is
    currently pressed, and closes it again. Returns False on any failure
    (device busy, unplugged, transient error) rather than raising — a
    poller must never crash the app over a momentary hardware hiccup.

    Re-fetches the option descriptor list before reading the raw value:
    confirmed against real hardware that a stale/cached descriptor set
    does not reflect a fresh button press. Reads the raw option value via
    dev.dev.get_option(index) rather than dev.scan, since SaneDev.scan is
    python-sane's own bound method (for triggering a scan) and shadows the
    SANE option of the same name.
    """
    try:
        import sane

        sane.init()
        dev = sane.open(device_name)
        try:
            opts = dev.get_options()
            scan_opt = next((o for o in opts if o[1] == "scan"), None)
            if scan_opt is None:
                return False
            return bool(dev.dev.get_option(scan_opt[0]))
        finally:
            dev.close()
    except Exception:
        return False
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `.venv/bin/python -c "import scanix500.menubar.button_watcher"`
Expected: no output, exit code 0

- [ ] **Step 3: Run the full existing test suite to confirm nothing broke**

Run: `.venv/bin/pytest -v`
Expected: all previously-passing tests still pass (this task adds no new automated tests)

- [ ] **Step 4: Commit**

```bash
git add src/scanix500/menubar/button_watcher.py
git commit -m "Add button_watcher for polling the physical Scan button"
```

---

### Task 4: Wire button polling into the menu bar app

**Files:**
- Modify: `src/scanix500/menubar/app.py`

**Interfaces:**
- Consumes: `scanix500.menubar.button_watcher.resolve_device_name`, `scanix500.menubar.button_watcher.button_pressed` (Task 3); `scanix500.menubar.profiles.HARDWARE_BUTTON_PROFILE_NAME` (Task 2).

This file is GUI code with NO automated tests, per the Global Constraints — same as Task 3. Implement in one pass, verify the import, run the full suite, commit.

- [ ] **Step 1: Refactor the scan-launch logic into a shared method**

Replace `_make_scan_handler` in `src/scanix500/menubar/app.py` with:

```python
    def _start_scan(self, profile: Profile) -> None:
        if self._scanning:
            return
        self._scanning = True
        self.title = SCANNING_TITLE
        threading.Thread(target=self._run_scan_thread, args=(profile,), daemon=True).start()

    def _make_scan_handler(self, profile: Profile):
        def handler(_sender):
            self._start_scan(profile)
        return handler
```

This changes nothing about menu-click behavior — it only extracts the body so Step 3 below can call the same logic from the button poller.

- [ ] **Step 2: Verify existing behavior is unchanged**

Run: `.venv/bin/python -c "import scanix500.menubar.app"`
Expected: no output, exit code 0

- [ ] **Step 3: Add the button-polling Timer**

Add the import at the top of `src/scanix500/menubar/app.py`:

```python
from scanix500.menubar.button_watcher import button_pressed, resolve_device_name
from scanix500.menubar.profiles import HARDWARE_BUTTON_PROFILE_NAME
```

(add `HARDWARE_BUTTON_PROFILE_NAME` to the existing `from scanix500.menubar.profiles import (...)` block rather than a second import line for that module)

In `ScanixMenuBarApp.__init__`, after `self._rebuild_menu()`, add:

```python
        self._button_device_name: str | None = None
        self._button_timer = rumps.Timer(self._poll_button, 1.5)
        self._button_timer.start()
```

Add the new method, anywhere among the other private methods:

```python
    def _poll_button(self, _sender):
        if self._button_device_name is None:
            self._button_device_name = resolve_device_name()
        if self._button_device_name is None:
            return
        if not button_pressed(self._button_device_name):
            return
        for profile in self.profiles:
            if profile.name == HARDWARE_BUTTON_PROFILE_NAME:
                self._start_scan(profile)
                return
```

- [ ] **Step 4: Verify the module imports cleanly**

Run: `.venv/bin/python -c "import scanix500.menubar.app"`
Expected: no output, exit code 0

- [ ] **Step 5: Run the full test suite to confirm nothing broke**

Run: `.venv/bin/pytest -v`
Expected: all previously-passing tests still pass (this task adds no new automated tests)

- [ ] **Step 6: Commit**

```bash
git add src/scanix500/menubar/app.py
git commit -m "Poll the physical Scan button and trigger the Hardware Button profile"
```

---

### Task 5: Manual hardware checklist

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a Phase 3 manual checklist section**

Add a new section to `README.md`, after the existing "Phase 2: Menu bar app" section's "Manual UI checklist":

```markdown
## Phase 3: Physical Scan-button trigger

No install step beyond what Phase 2 already needs — the button watcher is
built into the menu bar app (`scanix500-menubar`) and requires no extra
dependency.

### Manual checklist

`button_watcher.py` and the `rumps.Timer` wiring in `app.py` have no
automated tests, since they're live hardware/GUI code:

- [ ] Launch `scanix500-menubar` with an existing `profiles.json` from a
      Phase 2 install (i.e. one that does NOT yet have a "Hardware Button"
      profile): confirm the profile is added automatically (check
      `~/Library/Application Support/scanix500/profiles.json` after
      launch, or check the "Edit Profile…"/"Delete Profile…" submenus)
      without any manual step.
- [ ] Press the physical Scan button on the iX500 (with paper loaded):
      confirm a scan starts using the "Hardware Button" profile — icon
      changes to the scanning state, then a completion notification
      appears, exactly like clicking that profile in the menu would.
- [ ] Press the button while a scan is already in progress (from a menu
      click or a previous button press): confirm nothing happens (no
      double-scan, no crash) — same guard as clicking a profile mid-scan.
- [ ] Unplug the scanner while the menu bar app is running: confirm no
      crash, no error notification spam — the app just quietly stops
      detecting presses until it's plugged back in.
- [ ] Delete or rename the "Hardware Button" profile via the Delete/Edit
      Profile menu, then press the physical button: confirm it's a silent
      no-op (no crash, no notification) rather than triggering the wrong
      profile.
- [ ] While the menu bar app is running and polling, confirm another
      application (e.g. VueScan, Image Capture) can still open and use the
      scanner in between polls — the device is not held open continuously.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Add Phase 3 manual hardware checklist"
```
