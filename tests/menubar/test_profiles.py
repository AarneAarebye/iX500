from pathlib import Path

import pytest

from scanix500.menubar.profiles import (
    HARDWARE_BUTTON_PROFILE_NAME,
    Profile,
    add_profile,
    delete_profile,
    find_profile,
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

    assert [p.name for p in loaded] == ["Default", HARDWARE_BUTTON_PROFILE_NAME]
    assert path.exists()  # the seeded profiles were persisted


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

    assert [p.name for p in loaded] == ["Default", HARDWARE_BUTTON_PROFILE_NAME]
    assert [p.name for p in load_profiles(path)] == ["Default", HARDWARE_BUTTON_PROFILE_NAME]


def test_load_profiles_falls_back_to_default_on_unknown_field(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text('[{"name": "Old", "destination": "/tmp/old", "removed_field": true}]')

    loaded = load_profiles(path)

    assert [p.name for p in loaded] == ["Default", HARDWARE_BUTTON_PROFILE_NAME]


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
    # The migration was persisted, not just returned in memory — and it
    # survives a reload even though the one-time migration no longer re-runs.
    assert [p.name for p in load_profiles(path)] == ["Documents", HARDWARE_BUTTON_PROFILE_NAME]


def test_load_profiles_does_not_re_add_hardware_button_after_deletion(tmp_path):
    path = tmp_path / "profiles.json"
    save_profiles(path, [Profile(name="Documents", destination="/tmp/docs")])
    load_profiles(path)  # triggers the one-time migration, creates the marker

    # Simulate the user deleting the Hardware Button profile afterward.
    remaining = [Profile(name="Documents", destination="/tmp/docs")]
    save_profiles(path, remaining)

    reloaded = load_profiles(path)

    assert [p.name for p in reloaded] == ["Documents"]


def test_find_profile_returns_matching_profile_or_none():
    profiles = [Profile(name="Documents", destination="/tmp/docs")]

    assert find_profile(profiles, "Documents") == profiles[0]
    assert find_profile(profiles, "Missing") is None


def test_profiles_saved_before_the_new_fields_still_load(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text('[{"name": "Old", "destination": "/d", "skip_blank_filter": false, "skip_ocr": false, "split_on_blank": false}]')
    (tmp_path / ".hardware_button_seeded").touch()
    [profile] = load_profiles(path)
    assert (profile.duplex, profile.color_mode, profile.resolution, profile.keep_blank_pages) == (True, "color", 300, False)


def test_profile_scanner_defaults_to_the_ix500_and_old_files_still_load(tmp_path):
    import json
    from scanix500.menubar.profiles import load_profiles, save_profiles

    path = tmp_path / "profiles.json"
    path.write_text(json.dumps([{"name": "Old", "destination": "/d", "duplex": False}]))
    loaded = load_profiles(path)
    assert loaded[0].scanner == "sane:ix500"

    save_profiles(path, [Profile(name="Flat", destination="/d", scanner="sane:canoscan-8800f")])
    assert load_profiles(path)[0].scanner == "sane:canoscan-8800f"


def test_profile_format_and_area_default_and_old_files_still_load(tmp_path):
    import json
    from scanix500.menubar.profiles import load_profiles, save_profiles

    path = tmp_path / "profiles.json"
    path.write_text(json.dumps([{"name": "Old", "destination": "/d", "scanner": "sane:ix500"}]))
    loaded = load_profiles(path)
    assert (loaded[0].format, loaded[0].area) == ("pdf", "a4")

    save_profiles(path, [Profile(name="Photo", destination="/d", format="jpeg", area="10x15")])
    again = load_profiles(path)[0]
    assert (again.format, again.area) == ("jpeg", "10x15")


# --- scanner choice ---------------------------------------------------------

from scanix500 import scanners as _scanners  # noqa: E402
from scanix500.menubar.profiles import profile_for_scanner, scanner_choices  # noqa: E402


def test_scanner_choices_lists_known_scanners_ix500_first():
    choices = scanner_choices()
    assert choices[0] == (_scanners.IX500_ID, "ScanSnap iX500")
    assert choices == [(s.id, s.name) for s in _scanners.KNOWN_SCANNERS]
    assert (_scanners.CANOSCAN_8800F_ID, "Canon CanoScan 8800F") in choices


def _full_profile():
    return Profile(
        name="Work", destination="/tmp/w", skip_blank_filter=True, skip_ocr=True,
        split_on_blank=True, duplex=True, color_mode="gray", resolution=600,
        format="pdf", area="a4",
    )


def test_profile_for_8800f_forces_single_pages_and_keeps_the_rest():
    p = _full_profile()
    out = profile_for_scanner(p, _scanners.CANOSCAN_8800F_ID)
    assert out.scanner == _scanners.CANOSCAN_8800F_ID
    assert out.duplex is False
    assert out.split_on_blank is False
    assert (out.name, out.destination, out.skip_ocr, out.skip_blank_filter) == ("Work", "/tmp/w", True, True)
    assert (out.color_mode, out.format, out.area, out.resolution) == ("gray", "pdf", "a4", 600)


def test_profile_for_8800f_does_not_mutate_its_argument():
    p = _full_profile()
    profile_for_scanner(p, _scanners.CANOSCAN_8800F_ID)
    assert p == _full_profile()


def test_unoffered_values_fall_back(monkeypatch):
    entry = _scanners.get_scanner(_scanners.CANOSCAN_8800F_ID).entry
    monkeypatch.setitem(entry, "resolutions", [150, 300])
    monkeypatch.setitem(entry, "colorModes", ["color"])
    monkeypatch.setitem(entry, "formats", ["pdf"])
    monkeypatch.setitem(entry, "areas", ["a4"])
    p = Profile(name="x", destination="/d", color_mode="gray", resolution=600,
                format="png", area="a5")
    out = profile_for_scanner(p, _scanners.CANOSCAN_8800F_ID)
    assert (out.resolution, out.color_mode, out.format, out.area) == (300, "color", "pdf", "a4")


def test_offered_values_are_kept_for_8800f():
    p = Profile(name="x", destination="/d", color_mode="bw", resolution=1200,
                format="png", area="10x15")
    out = profile_for_scanner(p, _scanners.CANOSCAN_8800F_ID)
    assert (out.resolution, out.color_mode, out.format, out.area) == (1200, "bw", "png", "10x15")


def test_back_to_ix500_clamps_values_and_restores_duplex():
    p = Profile(name="x", destination="/d", scanner=_scanners.CANOSCAN_8800F_ID,
                duplex=False, resolution=1200, format="png", area="a5")
    out = profile_for_scanner(p, _scanners.IX500_ID)
    entry = _scanners.get_scanner(_scanners.IX500_ID).entry
    assert out.scanner == _scanners.IX500_ID
    assert out.duplex is True
    if entry.get("resolutions"):
        assert out.resolution in entry["resolutions"]
    if entry.get("formats"):
        assert out.format in entry["formats"]
    if entry.get("areas"):
        assert out.area in entry["areas"]
    assert out.name == "x" and out.destination == "/d"


def test_unknown_scanner_raises():
    with pytest.raises(ValueError):
        profile_for_scanner(Profile(name="x", destination="/d"), "sane:nope")
