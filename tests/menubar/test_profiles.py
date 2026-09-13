from pathlib import Path

import pytest

from scanix500.menubar.profiles import (
    HARDWARE_BUTTON_PROFILE_NAME,
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
        Profile(name=HARDWARE_BUTTON_PROFILE_NAME, destination="/tmp/docs"),
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


def test_add_profile_raises_for_duplicate_name():
    existing = [Profile(name="Documents", destination="/tmp/docs")]

    with pytest.raises(ValueError):
        add_profile(existing, Profile(name="Documents", destination="/tmp/elsewhere"))


def test_replace_profile_raises_when_renaming_onto_an_existing_name():
    existing = [
        Profile(name="Documents", destination="/tmp/docs"),
        Profile(name="Receipts", destination="/tmp/receipts"),
    ]

    with pytest.raises(ValueError):
        replace_profile(existing, "Documents", Profile(name="Receipts", destination="/tmp/docs"))


def test_replace_profile_allows_keeping_the_same_name():
    existing = [
        Profile(name="Documents", destination="/tmp/docs"),
        Profile(name="Receipts", destination="/tmp/receipts"),
    ]
    updated = Profile(name="Documents", destination="/tmp/new-docs", skip_ocr=True)

    result = replace_profile(existing, "Documents", updated)

    assert result == [updated, existing[1]]


def test_load_profiles_falls_back_to_default_when_file_is_corrupt(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("{not valid json")

    loaded = load_profiles(path)

    assert len(loaded) == 1
    assert loaded[0].name == "Default"
    assert load_profiles(path) == loaded  # the recovered default was persisted


def test_load_profiles_falls_back_to_default_on_unknown_field(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text('[{"name": "Old", "destination": "/tmp/old", "removed_field": true}]')

    loaded = load_profiles(path)

    assert len(loaded) == 1
    assert loaded[0].name == "Default"


def test_save_profiles_leaves_no_temp_files_behind(tmp_path):
    path = tmp_path / "profiles.json"

    save_profiles(path, [Profile(name="Documents", destination="/tmp/docs")])

    assert [p.name for p in tmp_path.iterdir()] == ["profiles.json"]


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


def test_load_profiles_adds_hardware_button_profile_when_missing_from_existing_file(tmp_path):
    path = tmp_path / "profiles.json"
    save_profiles(path, [Profile(name="Documents", destination="/tmp/docs")])

    loaded = load_profiles(path)

    assert [p.name for p in loaded] == ["Documents", HARDWARE_BUTTON_PROFILE_NAME]
    # The migration was persisted, not just returned in memory:
    assert [p.name for p in load_profiles(path)] == ["Documents", HARDWARE_BUTTON_PROFILE_NAME]
