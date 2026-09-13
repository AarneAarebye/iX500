from pathlib import Path

import pytest

from scanix500.menubar.profiles import (
    Profile,
    add_profile,
    delete_profile,
    load_profiles,
    replace_profile,
    save_profiles,
)


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "profiles.json"
    profiles = [
        Profile(name="Documents", destination="/tmp/docs", skip_ocr=False),
        Profile(name="Receipts", destination="/tmp/receipts", skip_ocr=True, split_on_blank=True),
    ]

    save_profiles(path, profiles)
    loaded = load_profiles(path)

    assert loaded == profiles


def test_load_profiles_seeds_default_when_file_missing(tmp_path):
    path = tmp_path / "does-not-exist" / "profiles.json"

    loaded = load_profiles(path)

    assert len(loaded) == 1
    assert loaded[0].name == "Default"
    assert path.exists()  # the seeded default was persisted


def test_add_profile_appends_to_the_list():
    existing = [Profile(name="Documents", destination="/tmp/docs")]
    new_profile = Profile(name="Receipts", destination="/tmp/receipts")

    result = add_profile(existing, new_profile)

    assert result == [existing[0], new_profile]
    assert existing == [Profile(name="Documents", destination="/tmp/docs")]  # original untouched


def test_replace_profile_updates_the_matching_entry_by_name():
    existing = [
        Profile(name="Documents", destination="/tmp/docs"),
        Profile(name="Receipts", destination="/tmp/receipts"),
    ]
    updated = Profile(name="Documents", destination="/tmp/new-docs", skip_ocr=True)

    result = replace_profile(existing, "Documents", updated)

    assert result == [updated, existing[1]]


def test_replace_profile_raises_for_unknown_name():
    existing = [Profile(name="Documents", destination="/tmp/docs")]

    with pytest.raises(ValueError):
        replace_profile(existing, "NoSuchProfile", Profile(name="X", destination="/tmp/x"))


def test_delete_profile_removes_the_matching_entry_by_name():
    existing = [
        Profile(name="Documents", destination="/tmp/docs"),
        Profile(name="Receipts", destination="/tmp/receipts"),
    ]

    result = delete_profile(existing, "Documents")

    assert result == [existing[1]]


def test_delete_profile_raises_for_unknown_name():
    existing = [Profile(name="Documents", destination="/tmp/docs")]

    with pytest.raises(ValueError):
        delete_profile(existing, "NoSuchProfile")


def test_delete_profile_raises_when_only_one_profile_remains():
    existing = [Profile(name="Documents", destination="/tmp/docs")]

    with pytest.raises(ValueError):
        delete_profile(existing, "Documents")
