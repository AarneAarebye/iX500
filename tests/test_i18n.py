import pytest

from scanix500 import i18n


@pytest.mark.parametrize("tags, expected", [
    (["de-DE"], "de"),
    (["fr"], "fr"),
    (["es_ES"], "es"),
    (["hy-AM"], "hy"),
    (["zh-Hans-CN"], "zh-Hans"),
    (["zh-CN"], "zh-Hans"),
    (["zh"], "zh-Hans"),
    (["zh-Hant-TW"], "zh-Hant"),
    (["zh-TW"], "zh-Hant"),
    (["zh_HK"], "zh-Hant"),
    (["it-IT", "de-CH"], "de"),
    (["it-IT"], "en"),
    ([], "en"),
])
def test_language_from_tags(tags, expected):
    assert i18n.language_from_tags(tags) == expected


@pytest.mark.parametrize("env, expected", [
    ({"LANG": "de_DE.UTF-8"}, "de"),
    ({"LANG": "hy_AM.UTF-8"}, "hy"),
    ({"LANG": "zh_TW.UTF-8"}, "zh-Hant"),
    ({"LANG": "C.UTF-8"}, "en"),
    ({"LANG": "POSIX"}, "en"),
    ({"LC_ALL": "fr_FR.UTF-8", "LANG": "de_DE.UTF-8"}, "fr"),
    ({"LC_MESSAGES": "es_ES@euro", "LANG": "de_DE.UTF-8"}, "es"),
    ({"LANGUAGE": "it:hy", "LANG": "de_DE.UTF-8"}, "hy"),
    ({"LANGUAGE": "", "LANG": "de_DE.UTF-8"}, "de"),
    ({}, "en"),
])
def test_language_from_env(env, expected):
    assert i18n.language_from_env(env) == expected


def test_choose_language_override_then_tags_then_env():
    assert i18n.choose_language({"SCANIX500_LANG": "hy", "LANG": "de_DE.UTF-8"}, ["fr"]) == "hy"
    assert i18n.choose_language({"SCANIX500_LANG": "xx", "LANG": "de_DE.UTF-8"}, ["fr"]) == "fr"
    assert i18n.choose_language({"LANG": "de_DE.UTF-8"}, []) == "de"
    assert i18n.choose_language({}, []) == "en"


def test_t_formats_and_falls_back_to_english(monkeypatch):
    monkeypatch.setattr(i18n, "_strings", {"Delete profile “{name}”?": {"de": "Profil „{name}“ löschen?"}})
    assert i18n.t("Delete profile “{name}”?", name="Work") == "Delete profile “Work”?"
    i18n.set_language("de")
    assert i18n.current_language() == "de"
    assert i18n.t("Delete profile “{name}”?", name="Work") == "Profil „Work“ löschen?"
    assert i18n.t("Not in the catalog") == "Not in the catalog"
    i18n.set_language("es")  # no Spanish entry: English
    assert i18n.t("Delete profile “{name}”?", name="Work") == "Delete profile “Work”?"


def test_set_language_rejects_unknown():
    i18n.set_language("xx")
    assert i18n.current_language() == "en"


def test_t_without_arguments_keeps_braces():
    assert i18n.t("Literal {braces} stay") == "Literal {braces} stay"


def test_strings_json_loads():
    assert isinstance(i18n.load_strings(), dict)
