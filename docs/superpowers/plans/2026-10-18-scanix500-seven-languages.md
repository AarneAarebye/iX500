# scanix500 in Seven Languages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The scanix500 menu bar app (menu, alerts, profile form, notifications) and `scanix500-server`'s command output appear in English, German, Spanish, French, Chinese (Simplified and Traditional) and Armenian.

**Architecture:** A small `scanix500.i18n` module holds the language choice and `t(key, **kwargs)`; keys are the English text with `str.format` placeholders, translations live in one JSON file `src/scanix500/strings.json` (`{"<English key>": {"de": "…", …}}`). The menu bar app picks the language from macOS's preferred languages (Foundation's `NSLocale.preferredLanguages()`), the server from the POSIX locale environment (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`); `SCANIX500_LANG` overrides both. Text is translated where it is shown; everything that travels to Dossiary or into logs stays English.

**Tech Stack:** Python 3.11+ standard library (`json`, `importlib.resources`, `ast` in tests), rumps/PyObjC (menu bar, macOS only), pytest.

Spec: Dossiary repo, `docs/superpowers/specs/2026-10-17-armenian-design.md`, sub-project 4.

## Global Constraints

- Languages: `en`, `de`, `es`, `fr`, `zh-Hans`, `zh-Hant`, `hy`. English is the key itself and has no entry in `strings.json`.
- Out of scope, stays English: everything the bridge or the server sends to Dossiary or to Dossiary Scan Helper (protocol answers, `ScanResult.message` as produced by `runner.py`), the `scanix500` scan command's own output (the runner and bridge parse it), log lines, argparse help texts, and the profile name "Hardware Button" (it identifies the profile the hardware button runs).
- Keys are the English text, written as a string literal directly as the first argument of `t(...)`, so a test can find them with `ast`. Placeholders are `str.format` fields (`{name}`, `{count}`, `{name!r}`); every translation has exactly its key's fields.
- No plural variants: count-dependent texts are phrased so one form fits every number.
- Form of address matches Dossiary and Dossiary Scan Helper: German "du", Spanish "usted", French "vous", Chinese "您", Armenian polite capitalised «Դուք/Ձեր». Terms: pair = koppeln / emparejar / associer / 配对 / 配對 / զուգակցել; browser = Browser / navegador / navigateur / 浏览器 / 瀏覽器 / դիտարկիչ.
- zh-Hant is zh-Hans converted with OpenCC `s2t` (as for Dossiary and the Scan Helper).
- Unknown or missing language → English. `t()` never raises for a missing translation.
- Tests: `.venv/bin/pytest -q` green after every task (baseline 318 passed). Tests run in English regardless of the machine's locale.

---

## File Structure

- Create `src/scanix500/i18n.py` — language detection, `set_language()`, `t()`.
- Create `src/scanix500/strings.json` — the translations.
- Create `tests/conftest.py` — every test starts in English.
- Create `tests/test_i18n.py` — module tests, catalog checks, source/catalog match.
- Modify `src/scanix500/menubar/app.py`, `menubar/profile_form.py`, `menubar/profiles.py`, `server/cli.py` — visible texts through `t()`, language chosen in each `main()`.
- Modify `README.md`, `docs/raspberry-pi.md`, `src/scanix500/__init__.py`, `pyproject.toml` (0.7.0).

---

### Task 1: The `i18n` module

**Files:**
- Create: `src/scanix500/i18n.py`, `src/scanix500/strings.json` (contents `{}` plus a trailing newline)
- Create: `tests/conftest.py`, `tests/test_i18n.py`

**Interfaces:**
- Produces: `LANGS: tuple[str, ...]`; `language_from_tags(tags: Iterable[str]) -> str`; `language_from_env(env: Mapping[str, str]) -> str`; `macos_preferred_languages() -> list[str]`; `set_language(lang: str) -> None`; `current_language() -> str`; `t(key: str, **kwargs) -> str`; `load_strings() -> dict[str, dict[str, str]]`; `choose_language(env: Mapping[str, str], tags: Iterable[str] = ()) -> str` (`SCANIX500_LANG` if it names a known language, else the first of `tags`, else `env`'s locale, else `"en"` — callers pass `tags` from macOS or nothing).

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py`:

```python
import pytest

from scanix500 import i18n


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    """Every test starts in English, whatever the machine's locale."""
    monkeypatch.setenv("SCANIX500_LANG", "en")
    i18n.set_language("en")
    yield
    i18n.set_language("en")
```

`tests/test_i18n.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest -q tests/test_i18n.py`
Expected: errors — `cannot import name 'i18n' from 'scanix500'`.

- [ ] **Step 3: Write `src/scanix500/i18n.py`**

```python
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
```

A Mac whose preferred languages are all unsupported (say Italian) falls through to the locale environment, which in a LaunchAgent is usually empty → English.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest -q tests/test_i18n.py` — Expected: all pass. Then `.venv/bin/pytest -q` — Expected: 318 + the new tests pass, the same 1 warning as before.

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/i18n.py src/scanix500/strings.json tests/conftest.py tests/test_i18n.py
git commit -m "i18n: language detection and t() for what scanix500 shows a person"
```

---

### Task 2: Visible texts go through `t()`; each program picks its language

**Files:**
- Modify: `src/scanix500/menubar/app.py`, `src/scanix500/menubar/profile_form.py`, `src/scanix500/menubar/profiles.py`, `src/scanix500/server/cli.py`
- Modify: `src/scanix500/strings.json` (the 55 keys, each with `{}`)
- Modify: `tests/test_i18n.py`

**Interfaces:**
- Consumes: `t`, `set_language`, `choose_language`, `macos_preferred_languages` (Task 1).
- Produces: the 55 keys below (Task 3 translates exactly these); `display_message(message: str) -> str` in `menubar/runner.py`.

- [ ] **Step 1: Failing test — the code and the catalog name the same keys**

Append to `tests/test_i18n.py`:

```python
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
```

Also add a size check, since the match test alone passes while both sides are empty:

```python
def test_the_catalog_has_the_expected_size():
    assert len(catalog()) == 55
```

Run: `.venv/bin/pytest -q tests/test_i18n.py` — Expected: FAIL (`0 == 55`).

- [ ] **Step 2: `menubar/runner.py` — notification text**

`notification_title()` is only used for the notification (the bridge never calls it), so it returns
the translated title: change its three returns to `t("Scan complete")`, `t("Scan partially completed")`,
`t("Scan failed")` (with `from scanix500.i18n import t` at the top). Its tests run in English, so they
stay as they are. Add after it:

```python
# The notification's message. ScanResult.message itself stays English: the bridge sends it to
# Dossiary, and runner and bridge recognise these exact texts.
def display_message(message: str) -> str:
    known = {
        "Scan complete": t("Scan complete"),
        "No pages scanned.": t("No pages scanned."),
        "All pages were blank; no PDF written.": t("All pages were blank; no PDF written."),
    }
    return known.get(message, message)
```

- [ ] **Step 3: `menubar/app.py`**

- `from scanix500.i18n import choose_language, macos_preferred_languages, set_language, t` and `from scanix500.menubar.runner import display_message` (add to the existing runner import).
- `super().__init__("scanix500", title=IDLE_TITLE)` → `super().__init__("scanix500", title=IDLE_TITLE, quit_button=t("Quit"))`.
- Notifications: `title="Background OCR unavailable"` → `title=t("Background OCR unavailable")`; `title="Scan bridge unavailable"` → `title=t("Scan bridge unavailable")`.
- Menu: `"Add Profile…"`, `"Edit Profile…"`, `"Delete Profile…"`, `"Set Bridge Scan Folder…"`, `"Pair a Browser…"`, `"Forget Paired Browsers…"` → each `t("…")`.
- `_on_scan_complete`: `rumps.notification(title=notification_title(result), subtitle="", message=display_message(result.message))`.
- `_pair_browser`: `title=t("Pair a Browser")`, `message=t("Enter this code in Dossiary:\n\n{code}\n\nIt's valid for 2 minutes.", code=f"{code[:3]} {code[3:]}")`.
- `_forget_paired_browsers`: `title=t("Forget Paired Browsers")`, `message=t("Forget all paired browsers ({count})? Dossiary will ask for a new pairing code before its next scan.", count=count)`, `ok=t("Forget")`, `cancel=t("Cancel")`.
- `_add_profile`: `rumps.alert(t("Add Profile"), str(e))`; edit: `rumps.alert(t("Edit Profile"), str(e))`; delete: `rumps.alert(t("Delete Profile"), t("Delete profile “{name}”?", name=name), ok=t("Delete"), cancel=t("Cancel"))` and `rumps.alert(t("Delete Profile"), str(e))`.
- `main()`: before `ScanixMenuBarApp().run()` add `set_language(choose_language(os.environ, macos_preferred_languages()))` (and `import os`).

- [ ] **Step 4: `menubar/profile_form.py`** — `from scanix500.i18n import t`; `window.setTitle_("Profile")` → `t("Profile")`; `"Name:"`, `"Destination:"`, `"Choose…"`, `"Use blank-page filter"`, `"Use OCR"`, `"Split on blank separator sheets"`, `"Cancel"`, `"OK"` → `t("…")`.

- [ ] **Step 5: `menubar/profiles.py`** — `from scanix500.i18n import t`; the five `ValueError` texts become:

```python
raise ValueError(t("A profile named {name!r} already exists", name=profile.name))
raise ValueError(t("No profile named {name!r}", name=name))
raise ValueError(t("A profile named {name!r} already exists", name=updated.name))
raise ValueError(t("No profile named {name!r}", name=name))
raise ValueError(t("Cannot delete the last remaining profile"))
```

(English output is unchanged: `{name!r}` gives the same `'Work'` the f-strings gave.)

- [ ] **Step 6: `server/cli.py`** — `from scanix500.i18n import choose_language, set_language, t`; in `main()`, first line: `set_language(choose_language(os.environ))`. Replace (left: current, right: new):

- `f"Fingerprint: {display_fingerprint(...)}"` → `t("Fingerprint: {fingerprint}", fingerprint=display_fingerprint(...))`
- `("Created the server's certificate." if made else "The server's certificate already exists.")` → `(t("Created the server's certificate.") if made else t("The server's certificate already exists."))`
- `f"No certificate in {cfg.tls_dir}. Create one with: sudo scanix500-server init"` → `t("No certificate in {folder}. Create one with: sudo scanix500-server init", folder=cfg.tls_dir)`
- `f"Pairing code: {code}  (valid {minutes} minutes)"` → `t("Pairing code: {code}  (valid {minutes} minutes)", code=code, minutes=minutes)`
- `"Check that Dossiary Scan Helper shows the same fingerprint before you enter the code."` → `t("…")`
- `f"Removed “{revoke}”. It has to pair again to scan."` → `t("Removed “{name}”. It has to pair again to scan.", name=revoke)`
- `f"No paired computer is called “{revoke}”."` → `t("No paired computer is called “{name}”.", name=revoke)`
- `"No computer is paired yet."` → `t("…")`
- `f"{row['name']}  paired {row['paired_at'] or '?'}  last used {row['last_used'] or 'never'}"` → `t("{name}  paired {paired}  last used {used}", name=row['name'], paired=row['paired_at'] or '?', used=row['last_used'] or t("never"))`
- `f"Name: {cfg.name}"` → `t("Name: {name}", name=cfg.name)`; `f"Port: {cfg.port}"` → `t("Port: {port}", port=cfg.port)`
- `"Certificate: missing (run: sudo scanix500-server init)"`, `"Fingerprint: (run with sudo to see it)"`, `"Paired computers: (run with sudo to see them)"` → `t("…")`
- `f"Scanner: {device}" if device else "Scanner: not found"` → `t("Scanner: {device}", device=device) if device else t("Scanner: not found")`
- `f"Paired computers: {_paired_count(cfg)}"` → `t("Paired computers: {count}", count=_paired_count(cfg))`

Unchanged: `f"scanix500-server {__version__}"`, the Avahi XML, argparse help texts, `logging.info(...)`, `print(str(e), ...)`.

- [ ] **Step 7: `strings.json` with the 55 keys** (each mapped to `{}`), in this order:

```
Add Profile…
Edit Profile…
Delete Profile…
Set Bridge Scan Folder…
Pair a Browser…
Forget Paired Browsers…
Quit
Background OCR unavailable
Scan bridge unavailable
Pair a Browser
Enter this code in Dossiary:\n\n{code}\n\nIt's valid for 2 minutes.
Forget Paired Browsers
Forget all paired browsers ({count})? Dossiary will ask for a new pairing code before its next scan.
Forget
Cancel
Add Profile
Edit Profile
Delete Profile
Delete profile “{name}”?
Delete
A profile named {name!r} already exists
No profile named {name!r}
Cannot delete the last remaining profile
Profile
Name:
Destination:
Choose…
Use blank-page filter
Use OCR
Split on blank separator sheets
OK
Scan complete
Scan partially completed
Scan failed
No pages scanned.
All pages were blank; no PDF written.
Created the server's certificate.
The server's certificate already exists.
Fingerprint: {fingerprint}
No certificate in {folder}. Create one with: sudo scanix500-server init
Pairing code: {code}  (valid {minutes} minutes)
Check that Dossiary Scan Helper shows the same fingerprint before you enter the code.
Removed “{name}”. It has to pair again to scan.
No paired computer is called “{name}”.
No computer is paired yet.
{name}  paired {paired}  last used {used}
never
Name: {name}
Port: {port}
Certificate: missing (run: sudo scanix500-server init)
Fingerprint: (run with sudo to see it)
Scanner: {device}
Scanner: not found
Paired computers: {count}
Paired computers: (run with sudo to see them)
```

(`\n` in the pairing key is a real newline in the JSON key, i.e. written `\n` inside the JSON string; two spaces in the pairing-code and clients lines are part of the key.) Write it with `json.dump(..., ensure_ascii=False, indent=2)` plus a trailing newline.

- [ ] **Step 8: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass (318 existing — English output is unchanged — plus Task 1's and the two new tests), same single warning. If an existing test fails on wording, the code changed the English: fix the code, not the test.

- [ ] **Step 9: Commit**

```bash
git add src tests
git commit -m "Menu bar app and scanix500-server: visible texts through t(); language per program"
```

---

### Task 3: The six translations

**Files:**
- Modify: `src/scanix500/strings.json`, `tests/test_i18n.py`

- [ ] **Step 1: Failing completeness test**

Append to `tests/test_i18n.py`:

```python
import re
import string

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


def test_a_translation_is_used(monkeypatch):
    i18n.set_language("de")
    assert i18n.t("Delete profile “{name}”?", name="Work") == "Profil „Work“ löschen?"
    i18n.set_language("hy")
    assert i18n.t("Scanner: not found") == "Սկաներ՝ չի գտնվել"
```

Run: `.venv/bin/pytest -q tests/test_i18n.py` — Expected: FAIL (`de missing` …).

- [ ] **Step 2: Fill in the translations**

Save as a scratch script outside the repo and run it from the repo root; values in the order de, es, fr, zh-Hans, zh-Hant, hy:

```python
import json

LANGS = ["de", "es", "fr", "zh-Hans", "zh-Hant", "hy"]
T = {
"Add Profile…": ["Profil hinzufügen…", "Añadir perfil…", "Ajouter un profil…", "添加配置…", "添加配置…", "Ավելացնել պրոֆիլ…"],
"Edit Profile…": ["Profil bearbeiten…", "Editar perfil…", "Modifier le profil…", "编辑配置…", "編輯配置…", "Խմբագրել պրոֆիլը…"],
"Delete Profile…": ["Profil löschen…", "Eliminar perfil…", "Supprimer le profil…", "删除配置…", "刪除配置…", "Ջնջել պրոֆիլը…"],
"Set Bridge Scan Folder…": ["Ordner für Bridge-Scans festlegen…", "Elegir la carpeta de escaneos del puente…", "Choisir le dossier des numérisations du pont…", "设置桥接扫描文件夹…", "設置橋接掃描文件夾…", "Ընտրել bridge-ի սկանավորումների թղթապանակը…"],
"Pair a Browser…": ["Browser koppeln…", "Emparejar un navegador…", "Associer un navigateur…", "配对浏览器…", "配對瀏覽器…", "Զուգակցել դիտարկիչ…"],
"Forget Paired Browsers…": ["Gekoppelte Browser vergessen…", "Olvidar los navegadores emparejados…", "Oublier les navigateurs associés…", "忘记已配对的浏览器…", "忘記已配對的瀏覽器…", "Մոռանալ զուգակցված դիտարկիչները…"],
"Quit": ["Beenden", "Salir", "Quitter", "退出", "退出", "Փակել"],
"Background OCR unavailable": ["OCR im Hintergrund nicht verfügbar", "OCR en segundo plano no disponible", "OCR en arrière-plan indisponible", "后台 OCR 不可用", "後臺 OCR 不可用", "Ֆոնային OCR-ը հասանելի չէ"],
"Scan bridge unavailable": ["Scan-Bridge nicht verfügbar", "Puente de escaneo no disponible", "Pont de numérisation indisponible", "扫描桥接不可用", "掃描橋接不可用", "Սկանավորման bridge-ը հասանելի չէ"],
"Pair a Browser": ["Browser koppeln", "Emparejar un navegador", "Associer un navigateur", "配对浏览器", "配對瀏覽器", "Զուգակցել դիտարկիչ"],
"Enter this code in Dossiary:\n\n{code}\n\nIt's valid for 2 minutes.": ["Gib diesen Code in Dossiary ein:\n\n{code}\n\nEr gilt 2 Minuten lang.", "Escriba este código en Dossiary:\n\n{code}\n\nEs válido durante 2 minutos.", "Saisissez ce code dans Dossiary :\n\n{code}\n\nIl est valable 2 minutes.", "请在 Dossiary 中输入此代码：\n\n{code}\n\n有效期为 2 分钟。", "請在 Dossiary 中輸入此代碼：\n\n{code}\n\n有效期爲 2 分鐘。", "Մուտքագրեք այս կոդը Dossiary-ում՝\n\n{code}\n\nԱյն վավեր է 2 րոպե։"],
"Forget Paired Browsers": ["Gekoppelte Browser vergessen", "Olvidar los navegadores emparejados", "Oublier les navigateurs associés", "忘记已配对的浏览器", "忘記已配對的瀏覽器", "Մոռանալ զուգակցված դիտարկիչները"],
"Forget all paired browsers ({count})? Dossiary will ask for a new pairing code before its next scan.": ["Alle gekoppelten Browser ({count}) vergessen? Dossiary fragt vor dem nächsten Scan nach einem neuen Kopplungscode.", "¿Olvidar todos los navegadores emparejados ({count})? Dossiary pedirá un nuevo código de emparejamiento antes del próximo escaneo.", "Oublier tous les navigateurs associés ({count}) ? Dossiary demandera un nouveau code d'association avant la prochaine numérisation.", "忘记所有已配对的浏览器（{count}）？Dossiary 会在下次扫描前要求新的配对码。", "忘記所有已配對的瀏覽器（{count}）？Dossiary 會在下次掃描前要求新的配對碼。", "Մոռանա՞լ բոլոր զուգակցված դիտարկիչները ({count})։ Հաջորդ սկանավորումից առաջ Dossiary-ն նոր զուգակցման կոդ կխնդրի։"],
"Forget": ["Vergessen", "Olvidar", "Oublier", "忘记", "忘記", "Մոռանալ"],
"Cancel": ["Abbrechen", "Cancelar", "Annuler", "取消", "取消", "Չեղարկել"],
"Add Profile": ["Profil hinzufügen", "Añadir perfil", "Ajouter un profil", "添加配置", "添加配置", "Ավելացնել պրոֆիլ"],
"Edit Profile": ["Profil bearbeiten", "Editar perfil", "Modifier le profil", "编辑配置", "編輯配置", "Խմբագրել պրոֆիլը"],
"Delete Profile": ["Profil löschen", "Eliminar perfil", "Supprimer le profil", "删除配置", "刪除配置", "Ջնջել պրոֆիլը"],
"Delete profile “{name}”?": ["Profil „{name}“ löschen?", "¿Eliminar el perfil «{name}»?", "Supprimer le profil « {name} » ?", "删除配置“{name}”？", "刪除配置“{name}”？", "Ջնջե՞լ «{name}» պրոֆիլը"],
"Delete": ["Löschen", "Eliminar", "Supprimer", "删除", "刪除", "Ջնջել"],
"A profile named {name!r} already exists": ["Ein Profil namens {name!r} gibt es schon", "Ya existe un perfil llamado {name!r}", "Un profil nommé {name!r} existe déjà", "已存在名为 {name!r} 的配置", "已存在名爲 {name!r} 的配置", "{name!r} անունով պրոֆիլ արդեն կա"],
"No profile named {name!r}": ["Kein Profil namens {name!r}", "No hay ningún perfil llamado {name!r}", "Aucun profil nommé {name!r}", "没有名为 {name!r} 的配置", "沒有名爲 {name!r} 的配置", "{name!r} անունով պրոֆիլ չկա"],
"Cannot delete the last remaining profile": ["Das letzte verbleibende Profil kann nicht gelöscht werden", "No se puede eliminar el último perfil", "Impossible de supprimer le dernier profil", "无法删除最后一个配置", "無法刪除最後一個配置", "Վերջին մնացած պրոֆիլը հնարավոր չէ ջնջել"],
"Profile": ["Profil", "Perfil", "Profil", "配置", "配置", "Պրոֆիլ"],
"Name:": ["Name:", "Nombre:", "Nom :", "名称：", "名稱：", "Անուն՝"],
"Destination:": ["Ziel:", "Destino:", "Destination :", "目标位置：", "目標位置：", "Նպատակակետ՝"],
"Choose…": ["Auswählen…", "Elegir…", "Choisir…", "选择…", "選擇…", "Ընտրել…"],
"Use blank-page filter": ["Filter für leere Seiten verwenden", "Usar el filtro de páginas en blanco", "Utiliser le filtre de pages blanches", "使用空白页过滤", "使用空白頁過濾", "Օգտագործել դատարկ էջերի զտիչը"],
"Use OCR": ["OCR verwenden", "Usar OCR", "Utiliser l'OCR", "使用 OCR", "使用 OCR", "Օգտագործել OCR"],
"Split on blank separator sheets": ["An leeren Trennblättern trennen", "Separar en las hojas separadoras en blanco", "Séparer aux feuilles blanches de séparation", "按空白分隔纸分割", "按空白分隔紙分割", "Բաժանել դատարկ բաժանարար թերթերով"],
"OK": ["OK", "Aceptar", "OK", "好", "好", "Լավ"],
"Scan complete": ["Scan abgeschlossen", "Escaneo completado", "Numérisation terminée", "扫描完成", "掃描完成", "Սկանավորումն ավարտված է"],
"Scan partially completed": ["Scan teilweise abgeschlossen", "Escaneo completado en parte", "Numérisation terminée en partie", "扫描部分完成", "掃描部分完成", "Սկանավորումը մասամբ ավարտված է"],
"Scan failed": ["Scan fehlgeschlagen", "Error al escanear", "Échec de la numérisation", "扫描失败", "掃描失敗", "Սկանավորումը ձախողվեց"],
"No pages scanned.": ["Keine Seiten gescannt.", "No se escaneó ninguna página.", "Aucune page numérisée.", "未扫描任何页面。", "未掃描任何頁面。", "Ոչ մի էջ չի սկանավորվել։"],
"All pages were blank; no PDF written.": ["Alle Seiten waren leer; kein PDF geschrieben.", "Todas las páginas estaban en blanco; no se creó ningún PDF.", "Toutes les pages étaient blanches ; aucun PDF créé.", "所有页面均为空白；未生成 PDF。", "所有頁面均爲空白；未生成 PDF。", "Բոլոր էջերը դատարկ էին․ PDF չի ստեղծվել։"],
"Created the server's certificate.": ["Das Zertifikat des Servers wurde erstellt.", "Se creó el certificado del servidor.", "Le certificat du serveur a été créé.", "已创建服务器证书。", "已創建服務器證書。", "Սերվերի վկայականը ստեղծվեց։"],
"The server's certificate already exists.": ["Das Zertifikat des Servers gibt es schon.", "El certificado del servidor ya existe.", "Le certificat du serveur existe déjà.", "服务器证书已存在。", "服務器證書已存在。", "Սերվերի վկայականն արդեն կա։"],
"Fingerprint: {fingerprint}": ["Fingerabdruck: {fingerprint}", "Huella digital: {fingerprint}", "Empreinte : {fingerprint}", "指纹：{fingerprint}", "指紋：{fingerprint}", "Մատնահետք՝ {fingerprint}"],
"No certificate in {folder}. Create one with: sudo scanix500-server init": ["Kein Zertifikat in {folder}. Erstelle eines mit: sudo scanix500-server init", "No hay certificado en {folder}. Cree uno con: sudo scanix500-server init", "Aucun certificat dans {folder}. Créez-en un avec : sudo scanix500-server init", "{folder} 中没有证书。请用以下命令创建：sudo scanix500-server init", "{folder} 中沒有證書。請用以下命令創建：sudo scanix500-server init", "{folder}-ում վկայական չկա։ Ստեղծեք այն՝ sudo scanix500-server init"],
"Pairing code: {code}  (valid {minutes} minutes)": ["Kopplungscode: {code}  (gültig {minutes} Minuten)", "Código de emparejamiento: {code}  (válido {minutes} minutos)", "Code d'association : {code}  (valable {minutes} minutes)", "配对码：{code}（{minutes} 分钟内有效）", "配對碼：{code}（{minutes} 分鐘內有效）", "Զուգակցման կոդ՝ {code}  (վավեր է {minutes} րոպե)"],
"Check that Dossiary Scan Helper shows the same fingerprint before you enter the code.": ["Prüfe, ob Dossiary Scan Helper denselben Fingerabdruck zeigt, bevor du den Code eingibst.", "Compruebe que Dossiary Scan Helper muestra la misma huella digital antes de escribir el código.", "Vérifiez que Dossiary Scan Helper affiche la même empreinte avant de saisir le code.", "输入代码前，请确认 Dossiary Scan Helper 显示相同的指纹。", "輸入代碼前，請確認 Dossiary Scan Helper 顯示相同的指紋。", "Կոդը մուտքագրելուց առաջ ստուգեք, որ Dossiary Scan Helper-ը նույն մատնահետքն է ցույց տալիս։"],
"Removed “{name}”. It has to pair again to scan.": ["„{name}“ entfernt. Er muss sich zum Scannen neu koppeln.", "Se quitó «{name}». Tendrá que emparejarse de nuevo para escanear.", "« {name} » retiré. Il devra s'associer de nouveau pour numériser.", "已移除“{name}”。它需要重新配对才能扫描。", "已移除“{name}”。它需要重新配對才能掃描。", "«{name}»-ը հեռացվեց։ Սկանավորելու համար այն պետք է կրկին զուգակցվի։"],
"No paired computer is called “{name}”.": ["Kein gekoppelter Computer heißt „{name}“.", "Ningún ordenador emparejado se llama «{name}».", "Aucun ordinateur associé ne s'appelle « {name} ».", "没有名为“{name}”的已配对电脑。", "沒有名爲“{name}”的已配對電腦。", "«{name}» անունով զուգակցված համակարգիչ չկա։"],
"No computer is paired yet.": ["Noch kein Computer gekoppelt.", "Aún no hay ningún ordenador emparejado.", "Aucun ordinateur associé pour l'instant.", "尚未配对任何电脑。", "尚未配對任何電腦。", "Դեռ ոչ մի համակարգիչ զուգակցված չէ։"],
"{name}  paired {paired}  last used {used}": ["{name}  gekoppelt {paired}  zuletzt benutzt {used}", "{name}  emparejado {paired}  último uso {used}", "{name}  associé {paired}  dernière utilisation {used}", "{name}  配对于 {paired}  最近使用 {used}", "{name}  配對於 {paired}  最近使用 {used}", "{name}  զուգակցված՝ {paired}  վերջին օգտագործում՝ {used}"],
"never": ["nie", "nunca", "jamais", "从未", "從未", "երբեք"],
"Name: {name}": ["Name: {name}", "Nombre: {name}", "Nom : {name}", "名称：{name}", "名稱：{name}", "Անուն՝ {name}"],
"Port: {port}": ["Port: {port}", "Puerto: {port}", "Port : {port}", "端口：{port}", "端口：{port}", "Պորտ՝ {port}"],
"Certificate: missing (run: sudo scanix500-server init)": ["Zertifikat: fehlt (ausführen: sudo scanix500-server init)", "Certificado: falta (ejecute: sudo scanix500-server init)", "Certificat : absent (exécutez : sudo scanix500-server init)", "证书：缺失（运行：sudo scanix500-server init）", "證書：缺失（運行：sudo scanix500-server init）", "Վկայական՝ բացակայում է (գործարկեք՝ sudo scanix500-server init)"],
"Fingerprint: (run with sudo to see it)": ["Fingerabdruck: (mit sudo ausführen, um ihn zu sehen)", "Huella digital: (ejecute con sudo para verla)", "Empreinte : (exécutez avec sudo pour la voir)", "指纹：（用 sudo 运行以查看）", "指紋：（用 sudo 運行以查看）", "Մատնահետք՝ (գործարկեք sudo-ով՝ տեսնելու համար)"],
"Scanner: {device}": ["Scanner: {device}", "Escáner: {device}", "Scanner : {device}", "扫描仪：{device}", "掃描儀：{device}", "Սկաներ՝ {device}"],
"Scanner: not found": ["Scanner: nicht gefunden", "Escáner: no encontrado", "Scanner : introuvable", "扫描仪：未找到", "掃描儀：未找到", "Սկաներ՝ չի գտնվել"],
"Paired computers: {count}": ["Gekoppelte Computer: {count}", "Ordenadores emparejados: {count}", "Ordinateurs associés : {count}", "已配对的电脑：{count}", "已配對的電腦：{count}", "Զուգակցված համակարգիչներ՝ {count}"],
"Paired computers: (run with sudo to see them)": ["Gekoppelte Computer: (mit sudo ausführen, um sie zu sehen)", "Ordenadores emparejados: (ejecute con sudo para verlos)", "Ordinateurs associés : (exécutez avec sudo pour les voir)", "已配对的电脑：（用 sudo 运行以查看）", "已配對的電腦：（用 sudo 運行以查看）", "Զուգակցված համակարգիչներ՝ (գործարկեք sudo-ով՝ տեսնելու համար)"],
}

path = "src/scanix500/strings.json"
c = json.load(open(path, encoding="utf-8"))
assert set(c) == set(T), set(c) ^ set(T)
for key, values in T.items():
    c[key] = dict(zip(LANGS, values))
with open(path, "w", encoding="utf-8") as f:
    json.dump(c, f, ensure_ascii=False, indent=2)
    f.write("\n")
```

Check zh-Hant against OpenCC once: `python3 -m venv /tmp/occ && /tmp/occ/bin/pip -q install opencc-python-reimplemented && /tmp/occ/bin/python -c "import json,opencc; c=json.load(open('src/scanix500/strings.json',encoding='utf-8')); cv=opencc.OpenCC('s2t'); print([k for k,v in c.items() if cv.convert(v['zh-Hans'])!=v['zh-Hant']])"` — Expected `[]`; for any key listed, take OpenCC's output.

- [ ] **Step 3: Run the tests**

Run: `.venv/bin/pytest -q` — Expected: all pass, same single warning.

- [ ] **Step 4: Try it**

Run: `SCANIX500_LANG=hy .venv/bin/python -c "from scanix500 import i18n; i18n.set_language(i18n.choose_language(__import__('os').environ)); print(i18n.t('Scanner: not found')); print(i18n.t('Pairing code: {code}  (valid {minutes} minutes)', code='123456', minutes=5))"`
Expected: Armenian lines.

- [ ] **Step 5: Commit**

```bash
git add src/scanix500/strings.json tests/test_i18n.py
git commit -m "German, Spanish, French, Chinese and Armenian for the menu bar app and scanix500-server"
```

---

### Task 4: Docs and version 0.7.0

**Files:**
- Modify: `src/scanix500/__init__.py` (`__version__ = "0.7.0"`), `pyproject.toml` (`version = "0.7.0"`), `README.md`, `docs/raspberry-pi.md`; any test pinning `0.6.2` as the current version (`grep -rn '0\.6\.2' src tests packaging`).

- [ ] **Step 1: Version** — bump both; run `.venv/bin/pytest -q`.

- [ ] **Step 2: `README.md`** — after the "Network scan server (Raspberry Pi)" section add:

```markdown
## Languages

The menu bar app (menu, alerts, the profile form, notifications) and `scanix500-server`'s output are
in English, German, Spanish, French, Chinese (Simplified and Traditional) and Armenian. The menu bar
app follows macOS's preferred languages (System Settings › General › Language & Region); the server
follows the locale (`LANG`, e.g. `de_DE.UTF-8`). `SCANIX500_LANG=hy` (or `de`, `es`, `fr`, `zh-Hans`,
`zh-Hant`, `en`) overrides both. Translations live in `src/scanix500/strings.json`, keyed by the
English text; `tests/test_i18n.py` fails on a missing translation, a changed `{field}`, or a key the
code doesn't use. What scanix500 sends to Dossiary, the `scanix500` command's own output and the logs
stay English.
```

- [ ] **Step 3: `docs/raspberry-pi.md`** — where the guide shows `scanix500-server status` / `pair`, add one sentence: "The commands answer in your locale's language (German, Spanish, French, Chinese or Armenian when `LANG` is set to one of them); `SCANIX500_LANG=en` forces English."

- [ ] **Step 4: Everything green, then commit**

Run: `.venv/bin/pytest -q` — Expected: all pass.

```bash
git add -A src pyproject.toml README.md docs/raspberry-pi.md tests
git commit -m "scanix500 0.7.0: seven languages"
```

---

## After the tasks (controller, not a task)

- Add the 55 texts to the Armenian review page as a "scanix500" group; put the English list on the Desktop for Grammarly.
- Release only with the user's OK: tag `v0.7.0` (the deb workflow builds the Pi package), GitHub release, `scripts/install-app.sh v0.7.0` on this Mac (into `/Applications/Dossiary/scanix500`); updating the Pi is the user's step.
