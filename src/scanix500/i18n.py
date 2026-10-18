"""The language of what scanix500 shows a person: the menu bar app's menu, alerts and
notifications, and scanix500-server's command output.

Keys are the English text with str.format fields; translations are in strings.json next to
this file. Only text shown to a person goes through t(): what the bridge or the server sends
to Dossiary or to Dossiary Scan Helper, and the scanix500 command's own output, stay English.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from importlib import resources

LANGS = ("en", "de", "es", "fr", "zh-Hans", "zh-Hant", "hy")
_SIMPLE = {"en", "de", "es", "fr", "hy"}
_HANT_REGIONS = {"tw", "hk", "mo"}

_language = "en"
_strings: dict[str, dict[str, str]] | None = None


def _match(tag: str) -> str | None:
    parts = tag.replace("_", "-").lower().split("-")
    if parts[0] == "zh":
        return "zh-Hant" if "hant" in parts[1:] or _HANT_REGIONS & set(parts[1:]) else "zh-Hans"
    return parts[0] if parts[0] in _SIMPLE else None


def _first_match(tags: Iterable[str]) -> str | None:
    return next((lang for lang in map(_match, tags) if lang), None)


def language_from_tags(tags: Iterable[str]) -> str:
    """The first supported language among BCP 47-ish tags ("de-DE", "zh-Hant-TW", "hy_AM"), else English."""
    return _first_match(tags) or "en"


def _locale_tag(value: str) -> str:
    return value.split(".")[0].split("@")[0]


def language_from_env(env: Mapping[str, str]) -> str:
    """The language a POSIX program would use: LANGUAGE (a colon list), else LC_ALL,
    LC_MESSAGES, LANG. "C" and "POSIX" mean English."""
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = env.get(name, "")
        if not value:
            continue
        if name == "LANGUAGE":
            tags = [_locale_tag(part) for part in value.split(":") if part]
            if not tags:
                continue
            return language_from_tags(tags)
        tag = _locale_tag(value)
        if tag in ("C", "POSIX"):
            return "en"
        return language_from_tags([tag])
    return "en"


def macos_preferred_languages() -> list[str]:
    """macOS's preferred languages (System Settings › General › Language & Region), or []
    where Foundation isn't available."""
    try:
        from Foundation import NSLocale
    except ImportError:
        return []
    return [str(tag) for tag in NSLocale.preferredLanguages()]


def choose_language(env: Mapping[str, str], tags: Iterable[str] = ()) -> str:
    """SCANIX500_LANG if it names a supported language, else the first supported tag (macOS's
    preferred languages), else the locale environment."""
    override = env.get("SCANIX500_LANG", "")
    if override in LANGS:
        return override
    return _first_match(tags) or language_from_env(env)


def set_language(lang: str) -> None:
    global _language
    _language = lang if lang in LANGS else "en"


def current_language() -> str:
    return _language


def load_strings() -> dict[str, dict[str, str]]:
    return json.loads(resources.files("scanix500").joinpath("strings.json").read_text(encoding="utf-8"))


def t(key: str, **kwargs) -> str:
    """The text for key in the current language (the English key when there is no translation),
    with kwargs filled in. Without kwargs the text is returned as it is."""
    global _strings
    if _strings is None:
        _strings = load_strings()
    text = key if _language == "en" else _strings.get(key, {}).get(_language, key)
    return text.format(**kwargs) if kwargs else text
