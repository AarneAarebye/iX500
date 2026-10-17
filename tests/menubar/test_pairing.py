import json

import pytest

from scanix500.menubar.pairing import (
    CODE_ATTEMPTS,
    CODE_LIFETIME_SECONDS,
    PairingError,
    PairingStore,
    bearer_token,
)


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path, clock) -> PairingStore:
    return PairingStore(tmp_path / "paired_browsers.json", clock=clock)


def test_new_code_is_six_digits(store):
    code = store.open_window()
    assert len(code) == 6 and code.isdigit()


def test_right_code_gives_a_working_token(store):
    code = store.open_window()
    token = store.pair(code)
    assert len(token) >= 22
    assert store.is_valid(token)
    assert not store.is_valid("something-else")
    assert not store.is_valid("")
    assert not store.is_valid(None)


def test_a_code_works_once(store):
    code = store.open_window()
    store.pair(code)
    with pytest.raises(PairingError):
        store.pair(code)


def test_no_window_open(store):
    with pytest.raises(PairingError):
        store.pair("123456")


def test_wrong_code_and_attempt_limit(store):
    code = store.open_window()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(CODE_ATTEMPTS - 1):
        with pytest.raises(PairingError):
            store.pair(wrong)
    # The last allowed attempt fails too and closes the window ...
    with pytest.raises(PairingError):
        store.pair(wrong)
    # ... so even the right code no longer works.
    with pytest.raises(PairingError):
        store.pair(code)


def test_code_expires(store, clock):
    code = store.open_window()
    clock.now += CODE_LIFETIME_SECONDS + 1
    with pytest.raises(PairingError):
        store.pair(code)


def test_new_window_replaces_the_old_code(store):
    first = store.open_window()
    second = store.open_window()
    if first != second:
        with pytest.raises(PairingError):
            store.pair(first)
    assert store.is_valid(store.pair(second))


def test_tokens_persist_and_only_hashes_are_stored(tmp_path, clock):
    path = tmp_path / "paired_browsers.json"
    store = PairingStore(path, clock=clock)
    token = store.pair(store.open_window())
    assert token not in path.read_text()
    assert len(json.loads(path.read_text())["token_hashes"]) == 1
    reloaded = PairingStore(path, clock=clock)
    assert reloaded.is_valid(token)


def test_forget_all(tmp_path, clock):
    path = tmp_path / "paired_browsers.json"
    store = PairingStore(path, clock=clock)
    token = store.pair(store.open_window())
    store.forget_all()
    assert not store.is_valid(token)
    assert not PairingStore(path, clock=clock).is_valid(token)
    assert store.paired_count() == 0


def test_corrupt_file_counts_as_no_pairings(tmp_path, clock):
    path = tmp_path / "paired_browsers.json"
    path.write_text("not json")
    assert PairingStore(path, clock=clock).paired_count() == 0


@pytest.mark.parametrize("header, expected", [
    ("Bearer abc-DEF_123", "abc-DEF_123"),
    (None, None),
    ("", None),
    ("Bearer", None),
    ("Bearer ", None),
    ("Basic abc", None),
    ("bearer abc", None),
    ("Bearer abc def", None),
])
def test_bearer_token(header, expected):
    assert bearer_token(header) == expected


def test_non_ascii_code_is_just_wrong(store):
    code = store.open_window()
    with pytest.raises(PairingError):
        store.pair("１２３４５６")
    assert store.is_valid(store.pair(code))


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
