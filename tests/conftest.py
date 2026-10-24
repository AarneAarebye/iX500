import pytest

from scanix500 import i18n


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    """Every test starts in English, whatever the machine's locale."""
    monkeypatch.setenv("SCANIX500_LANG", "en")
    i18n.set_language("en")
    yield
    i18n.set_language("en")


@pytest.fixture(autouse=True)
def _ix500_attached(monkeypatch):
    """Tests run on machines with no scanner: by default pretend the iX500 is plugged in."""
    monkeypatch.setattr("scanix500.scanners.usb_ids", lambda: {(0x04C5, 0x132B)})
