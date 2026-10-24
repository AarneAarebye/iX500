import sys

import pytest

from scanix500.menubar import button_watcher
from scanix500.menubar.button_watcher import (
    BUTTON_READERS, PIXMA_BUTTONS, _pixma_overrides, ix500_button_overrides, resolve_device_name,
)
from scanix500.scanners import CANOSCAN_8800F_ID, IX500_ID

DEVICES = [
    ("fujitsu:ScanSnap iX500:1203900", "FUJITSU", "ScanSnap iX500", "scanner"),
    ("pixma:04A91901", "CANON", "Canoscan 8800F", "multi-function peripheral"),
]


class _FakeSane:
    def init(self):
        pass

    def get_devices(self):
        return DEVICES


def test_resolve_finds_each_scanners_device(monkeypatch):
    monkeypatch.setitem(sys.modules, "sane", _FakeSane())
    assert resolve_device_name() == "fujitsu:ScanSnap iX500:1203900"  # the default is the iX500
    assert resolve_device_name(IX500_ID) == "fujitsu:ScanSnap iX500:1203900"
    assert resolve_device_name(CANOSCAN_8800F_ID) == "pixma:04A91901"


def test_resolve_returns_none_for_an_unknown_scanner_or_without_sane(monkeypatch):
    monkeypatch.setitem(sys.modules, "sane", _FakeSane())
    assert resolve_device_name("sane:nope") is None
    monkeypatch.setitem(sys.modules, "sane", None)  # import sane -> ImportError
    assert resolve_device_name(CANOSCAN_8800F_ID) is None


# Measured on the real 8800F (see the spec's section 5): `target` is the pressed
# button's number for about one poll and 0 otherwise.
@pytest.mark.parametrize("target, expected", [
    (0, None),
    (1, None),  # COPY
    (2, None),  # PHOTO/FILM
    (3, None),  # E-MAIL
    (4, {"color_mode": "color"}),  # COLOR
    (5, {"color_mode": "bw"}),  # BLACK & WHITE
    (6, {}),  # CUSTOM: the profile as it is
    (7, None),  # FINISH
    (8, None),
])
def test_pixma_buttons(target, expected):
    # button-1 blips to 1 while idle; it must not matter.
    assert _pixma_overrides({"button-1": 1, "button-2": 0, "target": target}) == expected


def test_pixma_overrides_without_a_target_is_no_press():
    assert _pixma_overrides({}) is None


def test_pixma_overrides_returns_a_copy():
    _pixma_overrides({"target": 4})["color_mode"] = "gray"
    assert PIXMA_BUTTONS[4] == {"color_mode": "color"}


def test_an_ix500_press_means_no_overrides(monkeypatch):
    monkeypatch.setattr(button_watcher, "button_pressed", lambda dev: True)
    assert ix500_button_overrides("fujitsu:1") == {}
    monkeypatch.setattr(button_watcher, "button_pressed", lambda dev: False)
    assert ix500_button_overrides("fujitsu:1") is None


def test_each_button_scanner_has_a_reader():
    assert BUTTON_READERS == {IX500_ID: ix500_button_overrides,
                              CANOSCAN_8800F_ID: button_watcher.pixma_button_overrides}


def test_pixma_button_overrides_never_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "sane", None)
    assert button_watcher.pixma_button_overrides("pixma:04A91901") is None
