import pytest

from scanix500 import i18n


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    """Every test starts in English, whatever the machine's locale."""
    monkeypatch.setenv("SCANIX500_LANG", "en")
    i18n.set_language("en")
    yield
    i18n.set_language("en")
