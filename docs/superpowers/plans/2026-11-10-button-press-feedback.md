# Button press feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A press of a CanoScan 8800F button on the Pi is announced at once: `/health` carries a press counter for paired helpers, the Mac helper plays a sound, and an optional Pi command hook allows speaker/LED feedback.

**Architecture:** A small thread-safe `ButtonEvents` object on the Pi (counter, last button, optional feedback command) is fed by `ButtonCollector` before a scan or finish starts and read by the `/health` handler for paired callers. The helper's existing `ButtonScanPickup` loop polls faster (1 s), sends its token on `/health`, compares `seq` and calls an `onButtonPress` callback; the app plays a system sound unless the menu switch is off.

**Tech Stack:** Python 3.11 (pytest) in `iX500` (`src/scanix500/server`, `menubar/bridge.py`); Swift 6 / SwiftPM / swift-testing / AppKit in `dossiary-scan-helper/macos`.

Spec: `docs/superpowers/specs/2026-11-10-button-press-feedback-design.md` (iX500 repo). Tasks 1-2 are in `/Users/aarneaarebye/Projects/Paperless/iX500`, Tasks 3-4 in `/Users/aarneaarebye/Projects/Paperless/dossiary-scan-helper`. Run Python tests with `python3 -m pytest` (see `AGENTS.md`), Swift tests with `macos/scripts/test.sh`.

## Global Constraints

- `/health` field: `"buttonEvent": {"seq": <int>, "button": "<name>"}`, only when button scans are enabled **and** the request has a valid pairing token; `button` is one of `color`, `bw`, `custom`, `finish` and is absent while `seq` is 0.
- `seq` is memory-only, starts at 0 on every server start, and is bumped **before** the scan or finish starts.
- `button_feedback_command` (server config, key in the `[server]` section, default empty): split like a shell command line (`shlex.split`), run **without a shell**, with the button name appended as the last argument, in a background thread, killed after 5 s; failures and timeouts are logged and never delay or fail the scan.
- Helper: idle poll interval 1 s (was 5 s). The first `seq` seen per server after start-up or reconnect is only remembered. A different `seq` (larger or smaller) with a known `button` calls the callback once. A server without `buttonEvent`, or a malformed one, is ignored.
- Sounds: one short system sound for `color`/`bw`/`custom`, a different one for `finish`; menu item "Sound on button press" (check mark, default on, stored in UserDefaults key `buttonPressSound`).
- User-visible helper text goes in `macos/Localization/Localizable.xcstrings` with all seven languages (en, de, es, fr, hy, zh-Hans, zh-Hant); user-visible Pi text goes in `strings.json` with all 7 languages (`tests/test_i18n.py` guards it).
- No tokens, paths or file names in logs; never run `defaults delete`.
- Versions: scanix500 0.10.1 (`pyproject.toml`, `src/scanix500/__init__.py`, the version strings in `tests/menubar/test_bridge.py`, README install example); helper `HelperVersion.string` 0.6.1.
- Commit style: `feat:`/`fix:`/`docs:`/`chore:`, each commit ending with the attribution lines from the session reminder.

---

### Task 1: ButtonEvents and the collector hook (Pi)

**Files:**
- Create: `src/scanix500/server/button_events.py`
- Modify: `src/scanix500/server/config.py`, `src/scanix500/server/buttons.py`
- Test: `tests/server/test_button_events.py` (new), `tests/server/test_buttons.py`, `tests/server/test_config_tls.py`

**Interfaces:**
- Produces: `ButtonEvents(command: list[str] | None = None, run=subprocess.run)` with `record(button: str) -> None`, `snapshot() -> dict` (`{"seq": 0}` before the first press, else `{"seq": n, "button": name}`); `ServerConfig.button_feedback_command: str = ""`; `ButtonCollector(..., events: ButtonEvents | None = None)`.
- Consumes: existing `ButtonCollector.poll_once`, `FINISH`, `ServerConfig`, `load_config`.

- [ ] **Step 1: Write the failing tests** in `tests/server/test_button_events.py`:

```python
import threading
import time

from scanix500.server.button_events import ButtonEvents


def test_snapshot_before_and_after_presses():
    ev = ButtonEvents()
    assert ev.snapshot() == {"seq": 0}
    ev.record("color")
    ev.record("finish")
    assert ev.snapshot() == {"seq": 2, "button": "finish"}


def test_command_runs_with_the_button_name_and_no_shell():
    calls = []
    done = threading.Event()

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        done.set()

    ev = ButtonEvents(command=["/usr/bin/say", "-v", "x"], run=fake_run)
    ev.record("bw")
    assert done.wait(2)
    argv, kw = calls[0]
    assert argv == ["/usr/bin/say", "-v", "x", "bw"]
    assert kw["timeout"] == 5 and not kw.get("shell")


def test_failing_or_hanging_command_never_blocks_or_raises():
    def boom(argv, **kw):
        raise OSError("nope")

    ev = ButtonEvents(command=["x"], run=boom)
    started = time.time()
    ev.record("color")
    assert time.time() - started < 0.5
    assert ev.snapshot()["seq"] == 1


def test_no_command_runs_nothing():
    ev = ButtonEvents(command=None, run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    ev.record("custom")
```

Add to `tests/server/test_config_tls.py` (follow its existing style for writing a config file and calling `load_config`): `button_feedback_command` defaults to `""`, is read from the file, and a value with an unbalanced quote (`'say "x`) raises `ValueError` naming the key. Add to `tests/server/test_buttons.py` (the `Rig` takes an optional `events`; add `events=None` to `Rig.__init__` and pass `events=events` in `make`): a COLOR press records `color` **before** the scan runs (the fake `_run` appends `events.snapshot()["seq"]` to a list; assert it was 1 inside the scan), CUSTOM records `custom`, BLACK & WHITE `bw`, FINISH `finish`; no press (read returns None) records nothing.

- [ ] **Step 2: Run to verify failure.** `python3 -m pytest tests/server/test_button_events.py tests/server/test_buttons.py tests/server/test_config_tls.py -q` → import/attribute errors.

- [ ] **Step 3: Implement.**

`src/scanix500/server/button_events.py`:

```python
"""A press of an 8800F button, announced at once: a counter for /health and
an optional administrator-configured command (speaker, LED)."""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)

COMMAND_TIMEOUT_SECONDS = 5


class ButtonEvents:
    def __init__(self, command: Sequence[str] | None = None, run: Callable = subprocess.run):
        self._command = list(command) if command else None
        self._run = run
        self._lock = threading.Lock()
        self._seq = 0
        self._button: str | None = None

    def record(self, button: str) -> None:
        with self._lock:
            self._seq += 1
            self._button = button
        if self._command:
            threading.Thread(target=self._feedback, args=(button,), daemon=True).start()

    def snapshot(self) -> dict:
        with self._lock:
            if self._button is None:
                return {"seq": self._seq}
            return {"seq": self._seq, "button": self._button}

    def _feedback(self, button: str) -> None:
        try:
            self._run([*self._command, button], timeout=COMMAND_TIMEOUT_SECONDS, check=False,
                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:  # noqa: BLE001 - feedback must never matter to a scan
            logger.warning("Button feedback command failed: %s", type(exc).__name__)
```

`config.py`: add field `button_feedback_command: str = ""` to `ServerConfig`; in `load_config` read `raw_cmd = section.get("button_feedback_command", "").strip()`, validate with `shlex.split(raw_cmd)` (import `shlex`; on `ValueError` raise `ValueError(f"{path}: button_feedback_command is not a valid command line: …")`) and pass `button_feedback_command=raw_cmd` to the constructor.

`buttons.py`: add constructor parameter `events: ButtonEvents | None = None` (import from `scanix500.server.button_events`), store `self._events`. In `poll_once`, after `hit = self._read_buttons()`:

```python
        if hit is not None and self._events is not None:
            self._events.record("finish" if hit == FINISH else _button_name(hit))
```

with a module-level helper:

```python
def _button_name(overrides: dict) -> str:
    return {"color": "color", "bw": "bw"}.get(overrides.get("color_mode"), "custom")
```

(`CUSTOM` is `{}`; keep it before `finish_now`/`scan_page` calls so the record precedes the scan.)

- [ ] **Step 4: Run to verify pass**, plus `python3 -m pytest tests/server -q`.
- [ ] **Step 5: Commit** `feat: announce 8800F button presses (counter and optional command)`.

---

### Task 2: /health, wiring, status, docs, version 0.10.1 (Pi)

**Files:**
- Modify: `src/scanix500/menubar/bridge.py` (`make_handler_class`, `/health`), `src/scanix500/server/runtime.py` (`start_remote_server`), `src/scanix500/server/cli.py` (wiring, `_button_status_lines`), `strings.json`, `pyproject.toml`, `src/scanix500/__init__.py`, `tests/menubar/test_bridge.py` (version strings), `README.md`, `docs/raspberry-pi.md`, `AGENTS.md`
- Test: `tests/server/test_button_scan_endpoints.py`, `tests/server/test_cli.py`

**Interfaces:**
- Consumes: Task 1 `ButtonEvents.snapshot()`, `ServerConfig.button_feedback_command`.
- Produces: `make_handler_class(..., button_events: ButtonEvents | None = None)`; `start_remote_server(..., button_events=None)`.

- [ ] **Step 1: Failing tests** in `tests/server/test_button_scan_endpoints.py`: extend `_serve(tmp_path, store, remote=True, events=None)` to pass `button_events=events`. Tests: (a) with events and a valid token, `/health` JSON has `buttonEvent == {"seq": 0}`; after `events.record("color")` it equals `{"seq": 1, "button": "color"}`; (b) the same request without a token has **no** `buttonEvent` key; (c) with an invalid token, no key; (d) without a store (`_serve(tmp_path, None, events=events)`) no key even with a token; (e) `buttonScans` stays as before. In `tests/server/test_cli.py` (follow how the existing status tests build a config): `scanix500-server status` prints a line for a configured command and nothing extra when empty.

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** `bridge.py`: add `button_events=None` parameter to `make_handler_class`; in the `/health` branch after `health["buttonScans"] = True` add `if button_events is not None and token_ok: health["buttonEvent"] = button_events.snapshot()` (`token_ok` is already computed above). `runtime.py`: add `button_events=None` to `start_remote_server` and pass through. `cli.py`: build `events = ButtonEvents(shlex.split(cfg.button_feedback_command) if cfg.button_feedback_command else None)` in the non-fake branch (also in the fake branch so fake mode shows the field), pass `events=events` to `ButtonCollector` and `button_events=events` to `start_remote_server`. `_button_status_lines`: when `cfg.button_feedback_command` is set append `t("Button feedback command: {command}", command=cfg.button_feedback_command)`; add this string to `strings.json` in all 7 languages following the file's existing format (look at an entry such as "Waiting for pickup: {count}"). Bump version to 0.10.1 in `pyproject.toml`, `src/scanix500/__init__.py`, `tests/menubar/test_bridge.py` and the README install example (`grep -rn "0\.10\.0"` over `README.md docs tests src pyproject.toml`). Docs: README + `docs/raspberry-pi.md`: new "Button press feedback" section — the `/health` field (paired callers only), the `button_feedback_command` key with example `button_feedback_command = /usr/local/bin/beep` (script receives `color|bw|custom|finish`), 5 s limit, runs as user `scanix500`; AGENTS.md one bullet.
- [ ] **Step 4: Run** `python3 -m pytest -q` (whole suite) → pass.
- [ ] **Step 5: Commit** `feat: /health announces button presses to paired helpers; 0.10.1`.

---

### Task 3: Helper core — token on health, press detection, setting, protocol (helper repo)

**Files:**
- Modify: `macos/Sources/HelperCore/ButtonScanPickup.swift`, `macos/Sources/HelperCore/MenuModel.swift`, `macos/Sources/HelperCore/HelperRuntime.swift`, `PROTOCOL.md`
- Create: `macos/Sources/HelperCore/ButtonPressSetting.swift`
- Test: `macos/Tests/HelperCoreTests/ButtonScanPickupTests.swift`, `ButtonPressSettingTests.swift` (new), `MenuModelTests.swift`

**Interfaces:**
- Produces: `public enum ButtonPress: String, Sendable { case color, bw, custom, finish }`; `ButtonScanPickup.onButtonPress: @Sendable (ButtonPress) -> Void` (settable property like `onPickedUp`; init parameter `onButtonPress` with default no-op); `ButtonScanPickup.start(interval: TimeInterval = 1)`; `public final class ButtonPressSetting { init(defaults: UserDefaults = .standard, key: String = "buttonPressSound"); var isOn: Bool` (default `true`; `set(_ on: Bool)`) }`; `MenuAction.toggleButtonSound`; `MenuModel.entries(..., buttonSound: Bool? = nil, ...)` → when non-nil (and in the network-servers block) an action `.action(t("Sound on button press"), .toggleButtonSound, checked: buttonSound!, enabled: true)` directly after the "Folder for button scans…" action; `HelperRuntime.buttonPressSetting: ButtonPressSetting?` (nil in fake mode).

- [ ] **Step 1: Failing tests.** In `ButtonScanPickupTests.swift`: extend `Script` with `var buttonEvent: [String: Any]?` and `var requireTokenForEvent = true`, and have `/health` add `object["buttonEvent"] = buttonEvent` only when set and `r.token != nil` (mimics the server). Add a `presses` box to `Rig` collecting `ButtonPress` values via `onButtonPress`. Update the existing assertion `sent[0].token == nil` to `sent[0].token == "tok-scanner"` and `sent[1...]` accordingly (all requests carry the token). Tests: (1) first pass with `seq 3, button "color"` plays nothing; (2) next pass with `seq 4, button "bw"` plays `.bw` once; (3) `seq 4` again plays nothing; (4) `seq 1` (restart, smaller) with `finish` plays `.finish`; (5) a jump from 4 to 9 plays once; (6) `button` missing, unknown (`"x"`) or `buttonEvent` of wrong types (`"seq": "a"`) plays nothing and does not break picking up scans; (7) a server with `buttonScans: false` plays nothing; (8) after a failed health (`healthError = .unreachable`) the next successful pass only remembers (reconnect), then a later change plays; (9) two servers keep separate counters. `ButtonPressSettingTests.swift`: default on, set(false) persists in a fresh instance on the same suite, set(true) back. `MenuModelTests.swift`: with `buttonSound: true` the action `.action("Sound on button press", .toggleButtonSound, checked: true, enabled: true)` follows the folder action; with `false` it is unchecked; nil (and fake mode) omits it.
- [ ] **Step 2: Run to verify failure** (`macos/scripts/test.sh`).
- [ ] **Step 3: Implement.** In `ButtonScanPickup.pickUp(from:problems:)` the health request becomes `RemoteRequest(method: "GET", path: "/health", token: token, timeout: 3)`. After the `guard … buttonScans == true` line parse `health.json?["buttonEvent"]`: `seq` as `Int` (reject `Bool`/other types), `button` as `String` mapped via `ButtonPress(rawValue:)`; keep `private var lastSeq: [String: Int]` (key `server.id`, under `lock`). If the server has no stored `seq` → store it, no callback. If it differs from the stored value → store; if the button parsed, call `onButtonPress(button)` (outside the lock). A failed health request removes that server's entry so the next success is "first" again (do this in the `catch` of the health request path: `lock.withLock { lastSeq[server.id] = nil }`). `start(interval: TimeInterval = 1)`. `ButtonPressSetting` per interface; `HelperRuntime` creates `buttonPressSetting = ButtonPressSetting()` in the real branch (nil in fake) as a `public let`. `MenuAction` gets `toggleButtonSound`; `MenuModel` per interface. `PROTOCOL.md`: in the button-scans subsection document `buttonEvent` (sent only to paired callers; fields; counter semantics: memory-only, resets on server restart, helpers treat any change as a press and the first value as a baseline; unknown `button` values are ignored).
- [ ] **Step 4: Run** `macos/scripts/test.sh` → all pass.
- [ ] **Step 5: Commit** `feat: the helper notices button presses and polls every second`.

---

### Task 4: Helper app — sound, menu switch, texts, version 0.6.1 (helper repo)

**Files:**
- Modify: `macos/Sources/App/AppDelegate.swift`, `macos/Sources/HelperCore/Version.swift`, `macos/Localization/Localizable.xcstrings`, `macos/README.md`, `AGENTS.md`, `docs/install-macos.md`, `macos/Tests/HelperCoreTests/TextsTests.swift` (only if it lists keys)
- Create: `macos/Sources/App/ButtonPressSound.swift`

**Interfaces:**
- Consumes: Task 3 `ButtonPress`, `ButtonScanPickup.onButtonPress`, `ButtonPressSetting`, `MenuAction.toggleButtonSound`, `MenuModel` `buttonSound:`.

- [ ] **Step 1:** Add the localization entry `"Sound on button press"` with all seven languages to `Localizable.xcstrings` (de "Ton bei Tastendruck", es "Sonido al pulsar un botón", fr "Son à l'appui sur un bouton", zh-Hans "按下按钮时发出声音", zh-Hant "按下按鈕時發出聲音", hy "Ձայն կոճակը սեղմելիս"); the localization tests (`python3 -m unittest discover -s scripts -p 'test_*.py'` and the Swift texts tests) must pass.
- [ ] **Step 2: Implement.** `ButtonPressSound.swift` (AppKit): `enum ButtonPressSound { static func play(_ press: ButtonPress) }` using `NSSound(named: press == .finish ? "Glass" : "Tink")?.play()` (ignore nil). `AppDelegate`: after the runtime is created set `runtime.buttonScanPickup?.onButtonPress = { [setting = runtime.buttonPressSetting] press in if setting?.isOn == true { ButtonPressSound.play(press) } }` (find where `onPickedUp` is wired with `PickupNotifier` and do it beside it); pass `buttonSound: runtime.buttonPressSetting?.isOn` to `MenuModel.entries`; handle `.toggleButtonSound` in `menuItemChosen` by `runtime.buttonPressSetting?.set(!(runtime.buttonPressSetting?.isOn ?? true))`. Bump `HelperVersion.string` to `"0.6.1"` and fix any test that pins the version. Docs: `macos/README.md` and `docs/install-macos.md` — one paragraph on the sound and the menu switch, mention that it needs a scan server 0.10.1 or newer; `AGENTS.md` bullet.
- [ ] **Step 3: Run** `macos/scripts/test.sh`, `python3 -m unittest discover -s scripts -p 'test_*.py'` (from `macos`), `swift build` for the app target; all pass.
- [ ] **Step 4: Commit** `feat: Dossiary Scan Helper 0.6.1 plays a sound on button presses`.
