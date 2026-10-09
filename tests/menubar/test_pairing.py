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
