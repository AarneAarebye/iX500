# scanix500 Server Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `scanix500-server`, a headless HTTPS scan server for a Raspberry Pi: protocol version 2 over the network, with button and code pairing, named and revocable clients, and certificate fingerprints — part 1 of `docs/superpowers/specs/2026-10-17-pi-scan-server-design.md`.

**Architecture:** A new `scanix500.server` package reuses the bridge's request handler (`menubar/bridge.py`), which gains a *remote mode* (web-page origins refused, `/health` with fingerprint, `/pair/button` endpoints, no CORS). Pairing gains client names, last use and revocation, plus a window file so the separate `scanix500-server pair` command can open a code window for the running service. A lock-based scan trigger replaces the menu bar app's, and a button watcher grants pending button pairings. TLS comes from Python's `ssl` with a certificate made by `openssl`.

**Tech Stack:** Python 3.11+ stdlib (`http.server`, `ssl`, `configparser`, `argparse`), existing scanix500 modules, `openssl` CLI, pytest.

## Global Constraints

- Nothing under `scanix500.server` may import `rumps`, `AppKit`, `Foundation` or `objc`, directly or through its imports.
- Default port **8767**, HTTPS only, listening on all interfaces (IPv4 and IPv6). The loopback HTTP bridge (8765) and the menu bar app keep working unchanged.
- State directory default `/var/lib/scanix500` (env `SCANIX500_STATE_DIR` overrides), config file default `/etc/scanix500/server.conf` (env `SCANIX500_CONFIG` overrides). Under the state dir: `tls/cert.pem`, `tls/key.pem` (key mode 0600), `paired_clients.json`, `pairing-window.json`, `ocr-jobs/`, `scans/`.
- In remote mode any request carrying an `Origin` header gets 403, and no CORS headers are ever sent.
- Without a valid token only `GET /health`, `POST /pair`, `POST /pair/button` and `GET /pair/button/<id>` answer.
- `/health` in remote mode adds `"remote": true`, `"fingerprint"` (full SHA-256 of the certificate's DER, 64 uppercase hex) and `"name"`; `"service": "scanix500-server"`.
- Displayed fingerprints: the first 32 hex digits in 8 groups of 4, joined by `-`.
- Button pairing: a request is pending 120 s; exactly one pending request when the button is pressed → granted; two pending at once → both refused; at most 10 requests per 60 s → 429.
- Code pairing from `scanix500-server pair`: 6 digits, valid **300 s**, 5 wrong attempts close the window.
- Scans are deleted from the Pi once their response is built; background OCR jobs keep the existing retention.
- Run tests with `.venv/bin/pytest` from the iX500 repo root.

---

### Task 1: Named, revocable clients and a shared pairing window

**Files:**
- Modify: `src/scanix500/menubar/pairing.py`
- Modify: `src/scanix500/menubar/fakes.py` (`FakePairing.pair` signature, `issue_token`)
- Test: `tests/menubar/test_pairing.py`

**Interfaces:**
- Produces: `PairingStore.pair(code: str, client: str = "") -> str`; `PairingStore.issue_token(client: str) -> str`; `PairingStore.clients() -> list[dict]` (keys `name`, `paired_at`, `last_used`, sorted by name); `PairingStore.revoke(name: str) -> int`; `SharedWindowPairingStore(path: Path, window_path: Path, clock=time.time, now=None)` with `open_window() -> str` (writes the window file) and `pair()` (reads it); `SERVER_CODE_LIFETIME_SECONDS = 300`. `FakePairing.pair(code, client="")`, `FakePairing.issue_token(client) -> str`.

- [ ] **Step 1: Write the failing tests** — append to `tests/menubar/test_pairing.py`:

```python
import json as _json
from datetime import datetime, timedelta, timezone

from scanix500.menubar.pairing import (
    CODE_ATTEMPTS, SERVER_CODE_LIFETIME_SECONDS, PairingError, PairingStore, SharedWindowPairingStore,
)


def _now_box(start):
    box = [start]
    return box, (lambda: box[0])


def test_clients_are_named_listed_and_revocable(tmp_path):
    t0 = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    box, now = _now_box(t0)
    store = PairingStore(tmp_path / "p.json", now=now)
    mac = store.pair(store.open_window(), client="Arne's MacBook")
    store.issue_token("Office iMac")
    names = [c["name"] for c in store.clients()]
    assert names == ["Arne's MacBook", "Office iMac"]
    assert store.clients()[0]["paired_at"] == t0.isoformat() and store.clients()[0]["last_used"] is None
    box[0] = t0 + timedelta(hours=2)
    assert store.is_valid(mac)
    assert store.clients()[0]["last_used"] == (t0 + timedelta(hours=2)).isoformat()
    reloaded = PairingStore(tmp_path / "p.json", now=now)
    assert [c["name"] for c in reloaded.clients()] == names
    assert reloaded.revoke("Arne's MacBook") == 1
    assert not reloaded.is_valid(mac)
    assert reloaded.revoke("nobody") == 0


def test_tokens_from_before_client_names_still_work(tmp_path):
    (tmp_path / "p.json").write_text(_json.dumps({"token_hashes": ["ab" * 32]}))
    store = PairingStore(tmp_path / "p.json")
    assert store.paired_count() == 1
    assert store.clients()[0]["name"] == "(paired before names were kept)"


def test_shared_window_opened_by_one_process_pairs_in_another(tmp_path):
    clock = [1000.0]
    cli = SharedWindowPairingStore(tmp_path / "p.json", tmp_path / "w.json", clock=lambda: clock[0])
    code = cli.open_window()
    assert len(code) == 6 and code.isdigit()
    assert code not in (tmp_path / "w.json").read_text()  # only a hash is stored
    service = SharedWindowPairingStore(tmp_path / "p.json", tmp_path / "w.json", clock=lambda: clock[0])
    token = service.pair(code, client="Office iMac")
    assert service.is_valid(token)
    assert not (tmp_path / "w.json").exists()
    with pytest.raises(PairingError, match="scanix500-server pair"):
        service.pair(code)


def test_shared_window_expires_and_closes_after_wrong_codes(tmp_path):
    clock = [1000.0]
    store = SharedWindowPairingStore(tmp_path / "p.json", tmp_path / "w.json", clock=lambda: clock[0])
    store.open_window()
    clock[0] += SERVER_CODE_LIFETIME_SECONDS + 1
    with pytest.raises(PairingError, match="scanix500-server pair"):
        store.pair("000000")
    code = store.open_window()
    wrong = "111111" if code != "111111" else "222222"
    for _ in range(CODE_ATTEMPTS):
        with pytest.raises(PairingError):
            store.pair(wrong)
    with pytest.raises(PairingError, match="scanix500-server pair"):
        store.pair(code)


def test_service_sees_a_revocation_made_by_another_process(tmp_path):
    service = SharedWindowPairingStore(tmp_path / "p.json", tmp_path / "w.json")
    token = service.issue_token("Old laptop")
    assert service.is_valid(token)
    cli = SharedWindowPairingStore(tmp_path / "p.json", tmp_path / "w.json")
    assert cli.revoke("Old laptop") == 1
    assert not service.is_valid(token)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/menubar/test_pairing.py -q`
Expected: ImportError (`SharedWindowPairingStore`, `SERVER_CODE_LIFETIME_SECONDS`).

- [ ] **Step 3: Implement** — in `src/scanix500/menubar/pairing.py`:

Add imports `from datetime import datetime, timedelta, timezone`. Add constants after `CODE_ATTEMPTS`:

```python
SERVER_CODE_LIFETIME_SECONDS = 300
LAST_USED_SAVE_INTERVAL = timedelta(hours=1)
UNNAMED_CLIENT = "(paired before names were kept)"
```

Replace the `PairingStore` class with:

```python
class PairingStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] | None = None):
        self._path = path
        self._clock = clock
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._code: str | None = None
        self._expires_at = 0.0
        self._attempts_left = 0
        self._hashes, self._clients = self._load()

    def _load(self) -> tuple[set[str], dict[str, dict]]:
        try:
            data = json.loads(self._path.read_text())
            hashes = {h for h in data.get("token_hashes", []) if isinstance(h, str)}
            clients = data.get("clients", {})
            clients = {h: c for h, c in clients.items() if h in hashes and isinstance(c, dict)} if isinstance(clients, dict) else {}
            return hashes, clients
        except (OSError, ValueError, AttributeError):
            return set(), {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), prefix=self._path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"token_hashes": sorted(self._hashes), "clients": self._clients}, f)
            os.replace(tmp_name, self._path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        self._after_save()

    def _after_save(self) -> None:
        """Hook for SharedWindowPairingStore (file ownership, change tracking)."""

    def _issue_locked(self, client: str) -> str:
        self._before_read()  # don't write back another process's revocation as if it never happened
        token = secrets.token_urlsafe(24)
        digest = _hash(token)
        self._hashes.add(digest)
        self._clients[digest] = {"name": (client or "").strip()[:200] or "unnamed",
                                 "paired_at": self._now().isoformat(), "last_used": None}
        self._save()
        return token

    def issue_token(self, client: str) -> str:
        """A new token for `client`, without a code (button pairing)."""
        with self._lock:
            return self._issue_locked(client)

    def open_window(self) -> str:
        """Starts a pairing window and returns its 6-digit code. Opening a
        new window replaces any earlier code."""
        with self._lock:
            self._code = f"{secrets.randbelow(1_000_000):06d}"
            self._expires_at = self._clock() + CODE_LIFETIME_SECONDS
            self._attempts_left = CODE_ATTEMPTS
            return self._code

    def pair(self, code: str, client: str = "") -> str:
        """Exchanges the open window's code for a new token. A code works
        once; a wrong code uses up an attempt, and the last one closes the
        window."""
        with self._lock:
            if self._code is None or self._clock() > self._expires_at or self._attempts_left <= 0:
                self._code = None
                raise PairingError(
                    "No pairing code is active. Choose “Pair a Browser…” in the scanix500 menu and enter the new code."
                )
            if not hmac.compare_digest(code.encode("utf-8"), self._code.encode("utf-8")):
                self._attempts_left -= 1
                if self._attempts_left <= 0:
                    self._code = None
                raise PairingError(
                    "That code isn't right. Check the code shown by “Pair a Browser…” in the scanix500 menu."
                )
            self._code = None
            return self._issue_locked(client)

    def _before_read(self) -> None:
        """Hook for SharedWindowPairingStore: pick up another process's changes."""

    def is_valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            self._before_read()
            digest = _hash(token)
            if digest not in self._hashes:
                return False
            record = self._clients.get(digest)
            if record is not None:
                now = self._now()
                last = record.get("last_used")
                if last is None or now - datetime.fromisoformat(last) >= LAST_USED_SAVE_INTERVAL:
                    record["last_used"] = now.isoformat()
                    self._save()
            return True

    def clients(self) -> list[dict]:
        with self._lock:
            self._before_read()
            rows = [{"name": self._clients.get(h, {}).get("name", UNNAMED_CLIENT),
                     "paired_at": self._clients.get(h, {}).get("paired_at"),
                     "last_used": self._clients.get(h, {}).get("last_used")} for h in self._hashes]
            return sorted(rows, key=lambda r: r["name"].lower())

    def revoke(self, name: str) -> int:
        """Removes every token paired under `name`; returns how many."""
        with self._lock:
            self._before_read()
            gone = [h for h in self._hashes if self._clients.get(h, {}).get("name", UNNAMED_CLIENT) == name]
            for h in gone:
                self._hashes.discard(h)
                self._clients.pop(h, None)
            if gone:
                self._save()
            return len(gone)

    def forget_all(self) -> None:
        with self._lock:
            self._hashes = set()
            self._clients = {}
            self._save()

    def paired_count(self) -> int:
        with self._lock:
            self._before_read()
            return len(self._hashes)


def _match_dir_owner(path: Path) -> None:
    """When run as root (`sudo scanix500-server pair`), hand a file written
    in the service's state folder to that folder's owner, so the service
    (user scanix500) can still read and replace it."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        st = path.parent.stat()
        try:
            os.chown(path, st.st_uid, st.st_gid)
        except OSError:
            pass


class SharedWindowPairingStore(PairingStore):
    """scanix500-server's pairing: the service and the `scanix500-server
    pair` / `clients` commands are separate processes, so the code window
    lives in a file (only the code's hash, its expiry in wall-clock time and
    the attempts left), and each process re-reads the token file when
    another one changed it."""

    def __init__(self, path: Path, window_path: Path, clock: Callable[[], float] = time.time,
                 now: Callable[[], datetime] | None = None):
        super().__init__(path, clock=clock, now=now)
        self._window_path = window_path
        self._seen_mtime = self._mtime()

    def _mtime(self) -> int | None:
        try:
            return self._path.stat().st_mtime_ns
        except OSError:
            return None

    def _before_read(self) -> None:
        if self._mtime() != self._seen_mtime:
            self._hashes, self._clients = self._load()
            self._seen_mtime = self._mtime()

    def _after_save(self) -> None:
        _match_dir_owner(self._path)
        self._seen_mtime = self._mtime()

    def _write_window(self, window: dict) -> None:
        self._window_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._window_path.parent), prefix=self._window_path.name, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(window, f)
        os.replace(tmp_name, self._window_path)
        _match_dir_owner(self._window_path)

    def open_window(self) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self._lock:
            self._write_window({"code_hash": _hash(code), "expires_at": self._clock() + SERVER_CODE_LIFETIME_SECONDS,
                                "attempts_left": CODE_ATTEMPTS})
        return code

    def pair(self, code: str, client: str = "") -> str:
        no_window = PairingError("No pairing code is active. Run “sudo scanix500-server pair” on the Raspberry Pi "
                                 "and enter the new code.")
        with self._lock:
            self._before_read()
            try:
                window = json.loads(self._window_path.read_text())
            except (OSError, ValueError):
                raise no_window from None
            if not isinstance(window, dict) or self._clock() > window.get("expires_at", 0) \
                    or window.get("attempts_left", 0) <= 0:
                self._window_path.unlink(missing_ok=True)
                raise no_window
            if not hmac.compare_digest(_hash(code), str(window.get("code_hash", ""))):
                window["attempts_left"] = window.get("attempts_left", 0) - 1
                if window["attempts_left"] <= 0:
                    self._window_path.unlink(missing_ok=True)
                else:
                    self._write_window(window)
                raise PairingError("That code isn't right. Check the code “scanix500-server pair” printed.")
            self._window_path.unlink(missing_ok=True)
            return self._issue_locked(client)
```

In `src/scanix500/menubar/fakes.py`, change `FakePairing.pair(self, code: str)` to `pair(self, code: str, client: str = "")` and add:

```python
    def issue_token(self, client: str) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._tokens.add(token)
        return token
```

- [ ] **Step 4: Run the pairing tests and the whole suite**

Run: `.venv/bin/pytest tests/menubar/test_pairing.py -q && .venv/bin/pytest -q`
Expected: all pass (existing pairing tests unchanged in behaviour).

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/menubar/pairing.py src/scanix500/menubar/fakes.py tests/menubar/test_pairing.py
git commit -m "Pairing: named, revocable clients; a pairing window shared between processes"
```

---

### Task 2: Button pairing

**Files:**
- Create: `src/scanix500/server/__init__.py` (empty), `src/scanix500/server/button_pairing.py`
- Test: `tests/server/__init__.py` (empty), `tests/server/test_button_pairing.py`

**Interfaces:**
- Consumes: `PairingStore.issue_token(client)` (Task 1).
- Produces: `ButtonPairing(issue_token: Callable[[str], str], clock=time.monotonic, limit_per_minute=10)`; `request(client: str) -> str` (raises `PairingRateLimited`); `status(request_id: str) -> tuple[str, str | None]` with states `"pending" | "granted" | "expired" | "conflict" | "unknown"` (the token is returned once, with `"granted"`); `has_pending() -> bool`; `press() -> bool`; constants `BUTTON_WINDOW_SECONDS = 120`.

- [ ] **Step 1: Write the failing tests** — `tests/server/test_button_pairing.py`:

```python
import pytest

from scanix500.server.button_pairing import BUTTON_WINDOW_SECONDS, ButtonPairing, PairingRateLimited


def make():
    clock = [0.0]
    issued = []
    def issue(client):
        issued.append(client)
        return f"token-{len(issued)}"
    return ButtonPairing(issue, clock=lambda: clock[0]), clock, issued


def test_one_pending_request_is_granted_by_a_press_once():
    bp, clock, issued = make()
    rid = bp.request("Office iMac")
    assert bp.has_pending() and bp.status(rid) == ("pending", None)
    assert bp.press() is True
    assert issued == ["Office iMac"]
    assert bp.status(rid) == ("granted", "token-1")
    assert bp.status(rid) == ("unknown", None)  # handed out once
    assert not bp.has_pending()


def test_a_press_with_nothing_pending_does_nothing():
    bp, _, issued = make()
    assert bp.press() is False and issued == []


def test_two_pending_requests_are_both_refused():
    bp, _, issued = make()
    a, b = bp.request("Mac A"), bp.request("Mac B")
    assert bp.status(a) == ("conflict", None) and bp.status(b) == ("conflict", None)
    assert bp.press() is False and issued == []


def test_a_request_expires_after_the_window():
    bp, clock, issued = make()
    rid = bp.request("Mac")
    clock[0] += BUTTON_WINDOW_SECONDS + 1
    assert bp.status(rid) == ("expired", None)
    assert not bp.has_pending()
    assert bp.press() is False and issued == []


def test_requests_are_rate_limited():
    bp, clock, _ = make()
    for i in range(10):
        bp.status(bp.request(f"Mac {i}"))
    with pytest.raises(PairingRateLimited):
        bp.request("one too many")
    clock[0] += 61
    bp.request("later")


def test_unknown_request_id():
    bp, _, _ = make()
    assert bp.status("nope") == ("unknown", None)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/server/test_button_pairing.py -q`
Expected: ModuleNotFoundError `scanix500.server`.

- [ ] **Step 3: Implement** — `src/scanix500/server/button_pairing.py`:

```python
"""Pairing a computer by pressing the iX500's Scan button.

A helper asks to pair (POST /pair/button); the request waits up to 2
minutes for the button. Pressing it grants the one pending request a
token; two requests pending at once are both refused, so nobody can slip
a second request in alongside the person standing at the scanner."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable

BUTTON_WINDOW_SECONDS = 120
FORGET_AFTER_SECONDS = 300


class PairingRateLimited(Exception):
    """Too many pairing requests in the last minute."""


class ButtonPairing:
    def __init__(self, issue_token: Callable[[str], str], clock: Callable[[], float] = time.monotonic,
                 limit_per_minute: int = 10):
        self._issue = issue_token
        self._clock = clock
        self._limit = limit_per_minute
        self._lock = threading.Lock()
        self._requests: dict[str, dict] = {}
        self._recent: list[float] = []

    def _tidy_locked(self) -> None:
        now = self._clock()
        for rid, r in list(self._requests.items()):
            if r["state"] == "pending" and now - r["created"] > BUTTON_WINDOW_SECONDS:
                r["state"] = "expired"
            if now - r["created"] > FORGET_AFTER_SECONDS:
                del self._requests[rid]
        self._recent = [t for t in self._recent if now - t < 60]

    def _pending_locked(self) -> list[dict]:
        return [r for r in self._requests.values() if r["state"] == "pending"]

    def request(self, client: str) -> str:
        with self._lock:
            self._tidy_locked()
            if len(self._recent) >= self._limit:
                raise PairingRateLimited()
            now = self._clock()
            self._recent.append(now)
            rid = secrets.token_urlsafe(16)
            self._requests[rid] = {"client": client, "created": now, "state": "pending", "token": None}
            pending = self._pending_locked()
            if len(pending) > 1:
                for r in pending:
                    r["state"] = "conflict"
            return rid

    def status(self, request_id: str) -> tuple[str, str | None]:
        with self._lock:
            self._tidy_locked()
            r = self._requests.get(request_id)
            if r is None:
                return "unknown", None
            if r["state"] == "granted":
                del self._requests[request_id]
                return "granted", r["token"]
            return r["state"], None

    def has_pending(self) -> bool:
        with self._lock:
            self._tidy_locked()
            return bool(self._pending_locked())

    def press(self) -> bool:
        """The Scan button was pressed: grants the one pending request."""
        with self._lock:
            self._tidy_locked()
            pending = self._pending_locked()
            if len(pending) != 1:
                for r in pending:
                    r["state"] = "conflict"
                return False
            r = pending[0]
            r["token"] = self._issue(r["client"])
            r["state"] = "granted"
            return True
```

Create empty `src/scanix500/server/__init__.py` and `tests/server/__init__.py`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/server -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/server tests/server
git commit -m "Server: pairing by pressing the iX500's Scan button"
```

---

### Task 3: Configuration and TLS

**Files:**
- Create: `src/scanix500/server/config.py`, `src/scanix500/server/tls.py`
- Test: `tests/server/test_config_tls.py`

**Interfaces:**
- Produces: `DEFAULT_REMOTE_PORT = 8767`; `ServerConfig` dataclass (`port: int`, `name: str`, `state_dir: Path`; properties `tls_dir`, `cert_path`, `key_path`, `pairing_path`, `window_path`, `ocr_jobs_path`, `scans_path`); `load_config(path: Path | None = None) -> ServerConfig` (raises `ValueError` with a readable message for a bad port). `generate_certificate(tls_dir: Path, common_name: str) -> bool` (False if one already exists); `certificate_sha256(cert_path: Path) -> str` (64 uppercase hex); `display_fingerprint(sha256_hex: str) -> str`; `server_context(cert_path, key_path) -> ssl.SSLContext`.

- [ ] **Step 1: Write the failing tests** — `tests/server/test_config_tls.py`:

```python
import ssl
import stat

import pytest

from scanix500.server.config import DEFAULT_REMOTE_PORT, load_config
from scanix500.server.tls import certificate_sha256, display_fingerprint, generate_certificate, server_context


def test_defaults_without_a_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANIX500_STATE_DIR", str(tmp_path / "state"))
    cfg = load_config(tmp_path / "missing.conf")
    assert cfg.port == DEFAULT_REMOTE_PORT == 8767
    assert cfg.name  # the host name
    assert cfg.cert_path == tmp_path / "state" / "tls" / "cert.pem"
    assert cfg.window_path == tmp_path / "state" / "pairing-window.json"


def test_config_file_values(tmp_path, monkeypatch):
    monkeypatch.delenv("SCANIX500_STATE_DIR", raising=False)
    conf = tmp_path / "server.conf"
    conf.write_text("[server]\nport = 9443\nname = Hallway scanner\nstate_dir = %s\n" % (tmp_path / "s"))
    cfg = load_config(conf)
    assert (cfg.port, cfg.name, cfg.state_dir) == (9443, "Hallway scanner", tmp_path / "s")


def test_bad_port_is_reported(tmp_path):
    conf = tmp_path / "server.conf"
    conf.write_text("[server]\nport = seventy\n")
    with pytest.raises(ValueError, match="port"):
        load_config(conf)


def test_certificate_is_made_once_and_loads(tmp_path):
    tls = tmp_path / "tls"
    assert generate_certificate(tls, "raspberrypi") is True
    assert generate_certificate(tls, "raspberrypi") is False
    assert stat.S_IMODE((tls / "key.pem").stat().st_mode) == 0o600
    digest = certificate_sha256(tls / "cert.pem")
    assert len(digest) == 64 and digest == digest.upper()
    shown = display_fingerprint(digest)
    assert shown.count("-") == 7 and shown.replace("-", "") == digest[:32]
    assert isinstance(server_context(tls / "cert.pem", tls / "key.pem"), ssl.SSLContext)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/server/test_config_tls.py -q`
Expected: ModuleNotFoundError `scanix500.server.config`.

- [ ] **Step 3: Implement** — `src/scanix500/server/config.py`:

```python
"""scanix500-server's settings: /etc/scanix500/server.conf (an INI file
with a [server] section) and the state folder, /var/lib/scanix500."""

from __future__ import annotations

import configparser
import os
import socket
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REMOTE_PORT = 8767
DEFAULT_CONFIG = Path("/etc/scanix500/server.conf")
DEFAULT_STATE_DIR = Path("/var/lib/scanix500")


@dataclass(frozen=True)
class ServerConfig:
    port: int
    name: str
    state_dir: Path

    @property
    def tls_dir(self) -> Path: return self.state_dir / "tls"
    @property
    def cert_path(self) -> Path: return self.tls_dir / "cert.pem"
    @property
    def key_path(self) -> Path: return self.tls_dir / "key.pem"
    @property
    def pairing_path(self) -> Path: return self.state_dir / "paired_clients.json"
    @property
    def window_path(self) -> Path: return self.state_dir / "pairing-window.json"
    @property
    def ocr_jobs_path(self) -> Path: return self.state_dir / "ocr-jobs"
    @property
    def scans_path(self) -> Path: return self.state_dir / "scans"


def load_config(path: Path | None = None) -> ServerConfig:
    path = path or Path(os.environ.get("SCANIX500_CONFIG", str(DEFAULT_CONFIG)))
    parser = configparser.ConfigParser()
    parser.read(path)  # a missing file just means the defaults
    section = parser["server"] if parser.has_section("server") else {}
    raw_port = section.get("port", str(DEFAULT_REMOTE_PORT))
    try:
        port = int(raw_port)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError(f"{path}: port must be a number from 1 to 65535, not “{raw_port}”.") from None
    name = (section.get("name") or "").strip() or socket.gethostname().split(".")[0]
    state = os.environ.get("SCANIX500_STATE_DIR") or section.get("state_dir") or str(DEFAULT_STATE_DIR)
    return ServerConfig(port=port, name=name, state_dir=Path(state))
```

`src/scanix500/server/tls.py`:

```python
"""The server's own certificate: made once with openssl (EC P-256, 10
years), its SHA-256 fingerprint (pinned by the helpers when they pair),
and the TLS context the server listens with."""

from __future__ import annotations

import hashlib
import os
import ssl
import subprocess
from pathlib import Path


def generate_certificate(tls_dir: Path, common_name: str) -> bool:
    cert, key = tls_dir / "cert.pem", tls_dir / "key.pem"
    if cert.exists() and key.exists():
        return False
    tls_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(tls_dir, 0o700)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "3650", "-subj", f"/CN={common_name}"],
                   check=True, capture_output=True)
    os.chmod(key, 0o600)
    return True


def certificate_sha256(cert_path: Path) -> str:
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text())
    return hashlib.sha256(der).hexdigest().upper()


def display_fingerprint(sha256_hex: str) -> str:
    """The first 128 bits in 8 groups of 4, for a person to compare."""
    head = sha256_hex[:32]
    return "-".join(head[i:i + 4] for i in range(0, 32, 4))


def server_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert_path), str(key_path))
    return context
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/server -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/server/config.py src/scanix500/server/tls.py tests/server/test_config_tls.py
git commit -m "Server: configuration file and its own TLS certificate"
```

---

### Task 4: The bridge's remote mode

**Files:**
- Modify: `src/scanix500/menubar/bridge.py` (`RealBackend`, `make_handler_class`)
- Test: `tests/server/test_remote_handler.py`

**Interfaces:**
- Consumes: `ButtonPairing` and `PairingRateLimited` (Task 2); `PairingStore.pair(code, client)` (Task 1).
- Produces: `RemoteOptions` dataclass in `bridge.py` (`fingerprint: str`, `name: str`, `button_pairing: ButtonPairing`); `make_handler_class(get_destination, trigger, pairing, backend, remote: RemoteOptions | None = None)`; `RealBackend(get_destination, trigger, ocr_jobs=None, keep_outputs=True)`; `REMOTE_NOT_PAIRED_MESSAGE`.

- [ ] **Step 1: Write the failing tests** — `tests/server/test_remote_handler.py` (plain HTTP on loopback here; TLS is Task 5):

```python
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from scanix500.menubar.bridge import RealBackend, RemoteOptions, make_handler_class
from scanix500.menubar.fakes import FakeBackend
from scanix500.menubar.pairing import PairingStore
from scanix500.menubar.runner import ScanResult
from scanix500.server.button_pairing import ButtonPairing

FP = "AB" * 32


@pytest.fixture
def server(tmp_path):
    pairing = PairingStore(tmp_path / "p.json")
    buttons = ButtonPairing(pairing.issue_token)
    handler = make_handler_class(lambda: str(tmp_path), None, pairing, FakeBackend(),
                                 remote=RemoteOptions(fingerprint=FP, name="raspberrypi", button_pairing=buttons))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", pairing, buttons
    httpd.shutdown()


def call(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read() or b"{}"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


def test_health_names_the_server_and_its_fingerprint(server):
    base, _, _ = server
    status, body, headers = call(f"{base}/health")
    assert status == 200
    assert body["remote"] is True and body["fingerprint"] == FP and body["name"] == "raspberrypi"
    assert body["service"] == "scanix500-server" and body["protocol"] == 2 and body["paired"] is False
    assert "Access-Control-Allow-Origin" not in headers


def test_any_web_page_origin_is_refused(server):
    base, _, _ = server
    for origin in ("null", "http://localhost:8080", "https://example.com"):
        status, _, headers = call(f"{base}/health", headers={"Origin": origin})
        assert status == 403 and "Access-Control-Allow-Origin" not in headers


def test_scanners_need_a_token(server):
    base, pairing, _ = server
    status, body, _ = call(f"{base}/scanners")
    assert status == 401 and "Add network scanner" in body["message"]
    token = pairing.issue_token("Mac")
    status, body, _ = call(f"{base}/scanners", headers={"Authorization": f"Bearer {token}"})
    assert status == 200 and body["scanners"]


def test_code_pairing_records_the_client_name(server):
    base, pairing, _ = server
    code = pairing.open_window()
    status, body, _ = call(f"{base}/pair", "POST", {"code": code, "client": "Office iMac"})
    assert status == 200 and body["token"]
    assert [c["name"] for c in pairing.clients()] == ["Office iMac"]


def test_button_pairing_round_trip(server):
    base, pairing, buttons = server
    status, body, _ = call(f"{base}/pair/button", "POST", {"client": "Office iMac"})
    assert status == 202
    rid = body["request"]
    status, body, _ = call(f"{base}/pair/button/{rid}")
    assert status == 202 and body["state"] == "pending"
    assert buttons.press()
    status, body, _ = call(f"{base}/pair/button/{rid}")
    assert status == 200 and body["token"] and pairing.is_valid(body["token"])
    status, _, _ = call(f"{base}/pair/button/{rid}")
    assert status == 404


def test_button_pairing_conflict_and_bad_requests(server):
    base, _, _ = server
    a = call(f"{base}/pair/button", "POST", {"client": "A"})[1]["request"]
    call(f"{base}/pair/button", "POST", {"client": "B"})
    assert call(f"{base}/pair/button/{a}")[0] == 409
    assert call(f"{base}/pair/button", "POST", {"client": 5})[0] == 400
    assert call(f"{base}/pair/button", "GET")[0] == 405


def test_button_pairing_rate_limit(server):
    base, _, _ = server
    for i in range(10):
        call(f"{base}/pair/button", "POST", {"client": f"M{i}"})
    assert call(f"{base}/pair/button", "POST", {"client": "X"})[0] == 429


def test_real_backend_without_keeping_outputs_deletes_the_scan(tmp_path):
    pdf = tmp_path / "scan_1.pdf"
    from scanix500.pdf_builder import build_pdf
    from PIL import Image
    build_pdf([Image.new("L", (100, 100), 0)], pdf, ocr=False)

    class Trigger:
        def trigger(self, profile):
            return ScanResult(ok=True, partial=False, message=str(pdf), output_paths=[str(pdf)])

    backend = RealBackend(lambda: str(tmp_path), Trigger(), ocr_jobs=False, keep_outputs=False)
    settings = {"extras": {}, "duplex": True, "colorMode": "color", "resolution": 300}
    status, body = backend.scan({}, settings)
    assert status == 200 and body["files"]
    assert not pdf.exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/server/test_remote_handler.py -q`
Expected: ImportError `RemoteOptions`.

- [ ] **Step 3: Implement** — in `src/scanix500/menubar/bridge.py`:

Add imports: `from dataclasses import dataclass` and, guarded to keep the menubar app free of server code at import time,

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from scanix500.server.button_pairing import ButtonPairing
```

Add after `NOT_PAIRED_MESSAGE`:

```python
REMOTE_NOT_PAIRED_MESSAGE = (
    "This computer isn't paired with this scan server. Choose “Add network scanner…” in "
    "Dossiary Scan Helper and press the Scan button on the iX500."
)


@dataclass(frozen=True)
class RemoteOptions:
    """scanix500-server's network mode: no web pages at all (any Origin is
    refused, no CORS), /health with the certificate fingerprint and server
    name, and pairing by the scanner's button."""
    fingerprint: str
    name: str
    button_pairing: "ButtonPairing"
```

`RealBackend.__init__` gains `keep_outputs: bool = True` (store `self._keep_outputs = keep_outputs`). In `RealBackend.scan`, pass `copy_to=path if self._keep_outputs else None` to `submit`, and after the OCR loop, before `return status, body`:

```python
        if not self._keep_outputs:
            # scanix500-server: the files went out in this response; the Pi keeps no copy.
            for path in result.output_paths:
                Path(path).unlink(missing_ok=True)
```

`make_handler_class` gains `remote: RemoteOptions | None = None`. Inside `Handler`:

- `_cors_headers`: first line `if remote is not None: return`.
- `_origin_ok`: replace the first check with

```python
            origin = self.headers.get("Origin")
            allowed = origin is None if remote is not None else origin_allowed(origin)
            if allowed:
                return True
```

and in remote mode use the message `"A network scan server doesn't accept requests from web pages."` for the 403.
- In `_dispatch`, after computing `path`/`job_id` and before `method = ENDPOINTS.get(path)`:

```python
            if remote is not None and (path == "/pair/button" or path.startswith("/pair/button/")):
                self._handle_button_pair(path)
                return
```

- The `/health` body in remote mode:

```python
            if url.path == "/health":
                health = {"ok": True, "helper": "scanix500", "version": __version__, "protocol": 2,
                          "paired": token_ok, "service": "scanix500-bridge"}
                if remote is not None:
                    health.update(service="scanix500-server", remote=True, fingerprint=remote.fingerprint,
                                  name=remote.name)
                self._send_json(200, health)
                return
```

- The 401 uses `REMOTE_NOT_PAIRED_MESSAGE if remote is not None else NOT_PAIRED_MESSAGE`.
- `_handle_pair` calls `pairing.pair(body["code"], body.get("client", ""))`.
- Add the method:

```python
        def _handle_button_pair(self, path: str) -> None:
            from scanix500.server.button_pairing import PairingRateLimited
            if path == "/pair/button":
                if self.command != "POST":
                    self._error(405, "/pair/button only answers POST.", allow="POST")
                    return
                try:
                    body = self._read_json()
                except _BodyError:
                    body = None
                if not isinstance(body, dict) or not isinstance(body.get("client"), str) or len(body["client"]) > 200:
                    self._error(400, "The request needs a \"client\" name of at most 200 characters.")
                    return
                try:
                    request_id = remote.button_pairing.request(body["client"])
                except PairingRateLimited:
                    self._error(429, "Too many pairing requests. Wait a minute, then try again.")
                    return
                self._send_json(202, {"request": request_id})
                return
            if self.command != "GET":
                self._error(405, f"{path} only answers GET.", allow="GET")
                return
            state, token = remote.button_pairing.status(path[len("/pair/button/"):])
            if state == "granted":
                self._send_json(200, {"ok": True, "token": token})
            elif state == "pending":
                self._send_json(202, {"state": "pending"})
            elif state == "expired":
                self._error(408, "Nobody pressed the Scan button within 2 minutes. Try again.")
            elif state == "conflict":
                self._error(409, "Another computer asked to pair at the same time. Try again, one at a time.")
            else:
                self._error(404, "This pairing request is unknown or already finished.")
```

- [ ] **Step 4: Run the new tests and the whole suite**

Run: `.venv/bin/pytest tests/server/test_remote_handler.py -q && .venv/bin/pytest -q`
Expected: all pass, including the existing bridge tests (loopback mode unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/menubar/bridge.py tests/server/test_remote_handler.py
git commit -m "Bridge: remote mode for the network server (no web pages, fingerprint, button pairing)"
```

---

### Task 5: The server process and the `scanix500-server` command

**Files:**
- Create: `src/scanix500/server/runtime.py`, `src/scanix500/server/cli.py`
- Modify: `pyproject.toml` (`[project.scripts]`), `README.md`, `CLAUDE.md`
- Test: `tests/server/test_runtime.py`, `tests/server/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `ServerScanTrigger(run=run_scan)` with `busy: bool` and `trigger(profile) -> ScanResult` (raises `ScanBusyError`); `ButtonWatcher(button_pairing, trigger, resolve=resolve_device_name, pressed=button_pressed, interval=1.5)` with `poll_once()`, `start()`, `stop()`; `start_remote_server(config, backend, pairing, remote: RemoteOptions, context: ssl.SSLContext) -> ThreadingHTTPServer`; console script `scanix500-server` → `scanix500.server.cli:main(argv=None) -> int` with subcommands `init`, `run [--fake-scanners]`, `pair`, `clients [revoke NAME]`, `status`.

- [ ] **Step 1: Write the failing tests** — `tests/server/test_runtime.py`:

```python
import json
import ssl
import subprocess
import sys
import threading
import urllib.request

import pytest

from scanix500.menubar.bridge import RemoteOptions, ScanBusyError
from scanix500.menubar.fakes import FakeBackend
from scanix500.menubar.pairing import SharedWindowPairingStore
from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult
from scanix500.server.button_pairing import ButtonPairing
from scanix500.server.config import ServerConfig
from scanix500.server.runtime import ButtonWatcher, ServerScanTrigger, start_remote_server
from scanix500.server.tls import certificate_sha256, generate_certificate, server_context

PROFILE = Profile(name="x", destination="/tmp")


def test_trigger_runs_one_scan_at_a_time():
    started, release = threading.Event(), threading.Event()
    def slow(profile):
        started.set(); release.wait(5)
        return ScanResult(True, False, "done", [])
    trigger = ServerScanTrigger(run=slow)
    t = threading.Thread(target=trigger.trigger, args=(PROFILE,)); t.start()
    started.wait(5)
    assert trigger.busy
    with pytest.raises(ScanBusyError):
        trigger.trigger(PROFILE)
    release.set(); t.join()
    assert not trigger.busy


def test_trigger_turns_a_crash_into_a_failed_result():
    def boom(profile): raise RuntimeError("scanner exploded")
    result = ServerScanTrigger(run=boom).trigger(PROFILE)
    assert not result.ok and "exploded" in result.message


def test_button_watcher_only_polls_while_a_request_waits():
    issued = []
    bp = ButtonPairing(lambda c: issued.append(c) or "tok")
    calls = []
    watcher = ButtonWatcher(bp, ServerScanTrigger(), resolve=lambda: "fujitsu:ix500",
                            pressed=lambda dev: calls.append(dev) or True)
    watcher.poll_once()
    assert calls == []  # nothing pending: the scanner isn't touched
    bp.request("Mac")
    watcher.poll_once()
    assert calls == ["fujitsu:ix500"] and issued == ["Mac"]


def test_server_answers_over_https_with_its_fingerprint(tmp_path):
    cfg = ServerConfig(port=0, name="raspberrypi", state_dir=tmp_path)
    generate_certificate(cfg.tls_dir, "raspberrypi")
    pairing = SharedWindowPairingStore(cfg.pairing_path, cfg.window_path)
    fp = certificate_sha256(cfg.cert_path)
    remote = RemoteOptions(fingerprint=fp, name=cfg.name, button_pairing=ButtonPairing(pairing.issue_token))
    httpd = start_remote_server(cfg, FakeBackend(), pairing, remote, server_context(cfg.cert_path, cfg.key_path))
    try:
        port = httpd.server_address[1]
        client = ssl.create_default_context(cafile=str(cfg.cert_path))
        client.check_hostname = False
        with urllib.request.urlopen(f"https://127.0.0.1:{port}/health", context=client) as resp:
            body = json.loads(resp.read())
        assert body["fingerprint"] == fp and body["remote"] is True
    finally:
        httpd.shutdown()


def test_server_package_never_imports_the_mac_app_libraries():
    code = ("import sys, scanix500.server.cli; "
            "print(any(m in sys.modules for m in ('rumps', 'AppKit', 'Foundation', 'objc')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"
```

`tests/server/test_cli.py`:

```python
import pytest

from scanix500.server.cli import main


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANIX500_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SCANIX500_CONFIG", str(tmp_path / "none.conf"))
    return tmp_path / "state"


def test_init_makes_the_certificate_once(state, capsys):
    assert main(["init"]) == 0
    first = capsys.readouterr().out
    assert "Fingerprint:" in first and (state / "tls" / "cert.pem").exists()
    assert main(["init"]) == 0
    assert "already exists" in capsys.readouterr().out


def test_run_refuses_to_start_without_a_certificate(state, capsys):
    assert main(["run", "--fake-scanners"]) == 1
    assert "scanix500-server init" in capsys.readouterr().err


def test_pair_prints_a_code_and_the_fingerprint(state, capsys):
    main(["init"]); capsys.readouterr()
    assert main(["pair"]) == 0
    out = capsys.readouterr().out
    assert "Pairing code:" in out and "5 minutes" in out and "Fingerprint:" in out
    assert (state / "pairing-window.json").exists()


def test_clients_list_and_revoke(state, capsys):
    from scanix500.menubar.pairing import SharedWindowPairingStore
    store = SharedWindowPairingStore(state / "paired_clients.json", state / "pairing-window.json")
    store.issue_token("Office iMac")
    assert main(["clients"]) == 0
    assert "Office iMac" in capsys.readouterr().out
    assert main(["clients", "revoke", "Office iMac"]) == 0
    assert main(["clients", "revoke", "Office iMac"]) == 1
    assert "No paired computer" in capsys.readouterr().err


def test_status(state, capsys, monkeypatch):
    monkeypatch.setattr("scanix500.server.cli.resolve_device_name", lambda: None)
    main(["init"]); capsys.readouterr()
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Port: 8767" in out and "Scanner: not found" in out and "Paired computers: 0" in out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/server/test_runtime.py tests/server/test_cli.py -q`
Expected: ModuleNotFoundError `scanix500.server.runtime` / `.cli`.

- [ ] **Step 3: Implement** — `src/scanix500/server/runtime.py`:

```python
"""The running server: one scan at a time, the Scan button watched only
while a pairing request waits, and the HTTPS listener."""

from __future__ import annotations

import socket
import ssl
import threading
from collections.abc import Callable
from http.server import ThreadingHTTPServer

from scanix500.menubar.bridge import RemoteOptions, ScanBusyError, make_handler_class
from scanix500.menubar.button_watcher import button_pressed, resolve_device_name
from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult, run_scan
from scanix500.server.button_pairing import ButtonPairing
from scanix500.server.config import ServerConfig


class ServerScanTrigger:
    """ScanTrigger for the server: runs the scan in the request's own
    thread; a second request while one runs gets ScanBusyError (409)."""

    def __init__(self, run: Callable[[Profile], ScanResult] = run_scan):
        self._run = run
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def trigger(self, profile: Profile) -> ScanResult:
        if not self._lock.acquire(blocking=False):
            raise ScanBusyError()
        try:
            try:
                return self._run(profile)
            except Exception as e:  # noqa: BLE001 - always answer
                return ScanResult(ok=False, partial=False, message=str(e), output_paths=[])
        finally:
            self._lock.release()


class ButtonWatcher:
    """Polls the iX500's Scan button only while a button-pairing request
    is waiting and no scan runs, so the scanner isn't opened every 1.5 s
    for nothing."""

    def __init__(self, button_pairing: ButtonPairing, trigger: ServerScanTrigger,
                 resolve: Callable[[], str | None] = resolve_device_name,
                 pressed: Callable[[str], bool] = button_pressed, interval: float = 1.5):
        self._pairing, self._trigger = button_pairing, trigger
        self._resolve, self._pressed, self._interval = resolve, pressed, interval
        self._device: str | None = None
        self._stop = threading.Event()

    def poll_once(self) -> None:
        if not self._pairing.has_pending() or self._trigger.busy:
            return
        if self._device is None:
            self._device = self._resolve()
            if self._device is None:
                return
        if self._pressed(self._device):
            self._pairing.press()

    def start(self) -> None:
        def loop():
            while not self._stop.wait(self._interval):
                try:
                    self.poll_once()
                except Exception:  # noqa: BLE001 - never stop watching over one hiccup
                    self._device = None
        threading.Thread(target=loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


class _DualStackServer(ThreadingHTTPServer):
    """Listens on IPv6 and IPv4 at once (helpers may reach the Pi either way)."""
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def start_remote_server(config: ServerConfig, backend, pairing, remote: RemoteOptions,
                        context: ssl.SSLContext) -> ThreadingHTTPServer:
    handler = make_handler_class(lambda: str(config.scans_path), None, pairing, backend, remote=remote)
    handler.timeout = 60  # a client that stalls mid-request can't hold a thread forever
    httpd = _DualStackServer(("::", config.port), handler)
    # The TLS handshake runs in each request's own thread, not in accept().
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
```

`src/scanix500/server/cli.py`:

```python
"""`scanix500-server`: the iX500 as a network scanner on a Raspberry Pi.

  init                  make the server's certificate (once; the package does this)
  run [--fake-scanners] the service itself (systemd starts it)
  pair                  print a 6-digit pairing code, valid 5 minutes
  clients               list paired computers; `clients revoke NAME` removes one
  status                version, port, fingerprint, scanner, paired computers
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from scanix500 import __version__
from scanix500.menubar.bridge import RealBackend, RemoteOptions
from scanix500.menubar.button_watcher import resolve_device_name
from scanix500.menubar.fakes import FakeBackend, FakePairing
from scanix500.menubar.ocr_jobs import OcrJobQueue
from scanix500.menubar.pairing import SERVER_CODE_LIFETIME_SECONDS, SharedWindowPairingStore
from scanix500.server.button_pairing import ButtonPairing
from scanix500.server.config import ServerConfig, load_config
from scanix500.server.runtime import ButtonWatcher, ServerScanTrigger, start_remote_server
from scanix500.server.tls import certificate_sha256, display_fingerprint, generate_certificate, server_context


def _store(cfg: ServerConfig) -> SharedWindowPairingStore:
    return SharedWindowPairingStore(cfg.pairing_path, cfg.window_path)


def _fingerprint_line(cfg: ServerConfig) -> str:
    return f"Fingerprint: {display_fingerprint(certificate_sha256(cfg.cert_path))}"


def cmd_init(cfg: ServerConfig) -> int:
    made = generate_certificate(cfg.tls_dir, cfg.name)
    print(("Created the server's certificate." if made else "The server's certificate already exists."))
    print(_fingerprint_line(cfg))
    return 0


def cmd_run(cfg: ServerConfig, fake: bool) -> int:
    if not (cfg.cert_path.exists() and cfg.key_path.exists()):
        print(f"No certificate in {cfg.tls_dir}. Create one with: sudo scanix500-server init", file=sys.stderr)
        return 1
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg.scans_path.mkdir(parents=True, exist_ok=True)
    if fake:
        pairing = FakePairing()
        backend = FakeBackend()
    else:
        pairing = _store(cfg)
        queue = OcrJobQueue(cfg.ocr_jobs_path)
        queue.start()
        trigger = ServerScanTrigger()
        backend = RealBackend(lambda: str(cfg.scans_path), trigger, ocr_jobs=queue, keep_outputs=False)
    buttons = ButtonPairing(pairing.issue_token)
    fingerprint = certificate_sha256(cfg.cert_path)
    remote = RemoteOptions(fingerprint=fingerprint, name=cfg.name, button_pairing=buttons)
    httpd = start_remote_server(cfg, backend, pairing, remote, server_context(cfg.cert_path, cfg.key_path))
    if not fake:
        ButtonWatcher(buttons, trigger).start()
    logging.info("scanix500-server %s listening on port %d as %s (%s)", __version__, cfg.port, cfg.name,
                 display_fingerprint(fingerprint))
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    try:
        stopped.wait()
    except KeyboardInterrupt:
        pass
    httpd.shutdown()
    return 0


def cmd_pair(cfg: ServerConfig) -> int:
    code = _store(cfg).open_window()
    minutes = SERVER_CODE_LIFETIME_SECONDS // 60
    print(f"Pairing code: {code}  (valid {minutes} minutes)")
    if cfg.cert_path.exists():
        print(_fingerprint_line(cfg))
        print("Check that Dossiary Scan Helper shows the same fingerprint before you enter the code.")
    return 0


def cmd_clients(cfg: ServerConfig, revoke: str | None) -> int:
    store = _store(cfg)
    if revoke is not None:
        if store.revoke(revoke):
            print(f"Removed “{revoke}”. It has to pair again to scan.")
            return 0
        print(f"No paired computer is called “{revoke}”.", file=sys.stderr)
        return 1
    rows = store.clients()
    if not rows:
        print("No computer is paired yet.")
    for row in rows:
        print(f"{row['name']}  paired {row['paired_at'] or '?'}  last used {row['last_used'] or 'never'}")
    return 0


def cmd_status(cfg: ServerConfig) -> int:
    print(f"scanix500-server {__version__}")
    print(f"Name: {cfg.name}")
    print(f"Port: {cfg.port}")
    print(_fingerprint_line(cfg) if cfg.cert_path.exists() else "Certificate: missing (run: sudo scanix500-server init)")
    device = resolve_device_name()
    print(f"Scanner: {device}" if device else "Scanner: not found")
    print(f"Paired computers: {_store(cfg).paired_count()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scanix500-server", description="The iX500 as a network scanner.")
    parser.add_argument("--version", action="version", version=f"scanix500-server {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="make the server's certificate")
    run = sub.add_parser("run", help="run the server")
    run.add_argument("--fake-scanners", action="store_true", help="serve the protocol's fake scanners (testing)")
    sub.add_parser("pair", help="print a pairing code")
    clients = sub.add_parser("clients", help="list paired computers")
    clients_sub = clients.add_subparsers(dest="action")
    revoke = clients_sub.add_parser("revoke", help="remove a paired computer")
    revoke.add_argument("name")
    sub.add_parser("status", help="show the server's state")
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.command == "init":
        return cmd_init(cfg)
    if args.command == "run":
        return cmd_run(cfg, args.fake_scanners)
    if args.command == "pair":
        return cmd_pair(cfg)
    if args.command == "clients":
        return cmd_clients(cfg, args.name if args.action == "revoke" else None)
    return cmd_status(cfg)


if __name__ == "__main__":
    sys.exit(main())
```

`pyproject.toml`, in `[project.scripts]` add:

```toml
scanix500-server = "scanix500.server.cli:main"
```

Then reinstall the dev environment so the script exists: `.venv/bin/pip install -e ".[dev,menubar]" -q`.

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `.venv/bin/pytest tests/server -q && .venv/bin/pytest -q`
Expected: all pass. Also `.venv/bin/scanix500-server --version` prints `scanix500-server 0.5.1`.

- [ ] **Step 5: Docs** — `README.md`: add a short section "Network scan server (Raspberry Pi)" before "## Run":

```markdown
## Network scan server (Raspberry Pi)

`scanix500-server` runs the same scanning as the menu bar app without a
screen, so an iX500 on a Raspberry Pi can be shared over the network:
Dossiary Scan Helper on each computer finds it, pairs once (by pressing
the iX500's Scan button, or with `sudo scanix500-server pair`), and lists
it next to local scanners. It listens over HTTPS on port 8767 and accepts
no requests from web pages. `scanix500-server status`, `clients` and
`clients revoke NAME` manage it. Installing it on a Pi as a Debian package
is described in `docs/raspberry-pi.md` (part 3 of
`docs/superpowers/specs/2026-10-17-pi-scan-server-design.md`).
```

`CLAUDE.md`, under "Key constraints", add:

```markdown
- **`scanix500.server` is the headless network server** (spec
  `docs/superpowers/specs/2026-10-17-pi-scan-server-design.md`). It reuses
  `bridge.py`'s handler in *remote mode* (`RemoteOptions`: any `Origin`
  header refused, no CORS, `/health` with `fingerprint`/`name`,
  `/pair/button`), `SharedWindowPairingStore` (a code window in a file so
  `scanix500-server pair` can open it for the running service; tokens named
  and revocable), `ServerScanTrigger` (one scan at a time) and
  `ButtonWatcher` (polls the button only while a button-pairing request
  waits). Nothing under `scanix500.server` may import rumps/AppKit --
  `tests/server/test_runtime.py` checks it.
```

- [ ] **Step 6: Commit**

```bash
git add src/scanix500/server tests/server pyproject.toml README.md CLAUDE.md
git commit -m "scanix500-server: the network scan server and its command"
```
