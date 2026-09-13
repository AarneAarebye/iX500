from pathlib import Path

from scanix500.menubar.profiles import Profile, load_profiles, save_profiles


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "profiles.json"
    profiles = [
        Profile(name="Documents", destination="/tmp/docs", skip_ocr=False),
        Profile(name="Receipts", destination="/tmp/receipts", skip_ocr=True, split_on_blank=True),
    ]

    save_profiles(path, profiles)
    loaded = load_profiles(path)

    assert loaded == profiles
