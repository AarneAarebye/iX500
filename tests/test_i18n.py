import re

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
    (["zh-Hans-HK"], "zh-Hans"),
    (["zh-Hans-TW"], "zh-Hans"),
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


import ast
import json
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "scanix500"


def source_keys() -> set[str]:
    keys = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                keys.add(node.args[0].value)
    return keys


def catalog() -> dict:
    return json.loads((SRC / "strings.json").read_text(encoding="utf-8"))


def test_every_key_in_the_code_is_in_the_catalog_and_back():
    used, known = source_keys(), set(catalog())
    assert sorted(used - known) == [], "used but not in strings.json"
    assert sorted(known - used) == [], "in strings.json but not used"


def test_the_catalog_has_the_expected_size():
    assert len(catalog()) == 55


FIELD = re.compile(r"\{[^{}]*\}")


def test_every_language_is_complete_with_the_same_fields():
    problems = []
    for key, translations in catalog().items():
        for lang in i18n.LANGS[1:]:
            value = translations.get(lang)
            if not value:
                problems.append(f"{key!r}: {lang} missing")
            elif sorted(FIELD.findall(value)) != sorted(FIELD.findall(key)):
                problems.append(f"{key!r}: {lang} fields {FIELD.findall(value)}")
    assert problems == []


def test_a_translation_is_used():
    i18n.set_language("de")
    assert i18n.t("Delete profile “{name}”?", name="Work") == "Profil „Work“ löschen?"
    i18n.set_language("hy")
    assert i18n.t("Scanner: not found") == "Սկաներ՝ չի գտնվել"


def test_t_survives_unreadable_catalog(monkeypatch):
    def boom():
        raise OSError("unreadable")
    monkeypatch.setattr(i18n, "load_strings", boom)
    monkeypatch.setattr(i18n, "_strings", None)
    i18n.set_language("de")
    try:
        assert i18n.t("Cancel") == "Cancel"
    finally:
        i18n.set_language("en")
