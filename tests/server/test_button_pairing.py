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
