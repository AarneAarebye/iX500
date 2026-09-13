from pathlib import Path

from scanix500.menubar.profiles import Profile, add_profile, load_profiles, save_profiles


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
