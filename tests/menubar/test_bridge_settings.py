from pathlib import Path

from scanix500.menubar.bridge_settings import (
    default_bridge_destination_path,
    load_bridge_destination,
    save_bridge_destination,
)


def test_load_returns_documents_scans_default_when_file_missing(tmp_path):
    path = tmp_path / "does-not-exist" / "bridge_destination.txt"

    loaded = load_bridge_destination(path)

    assert loaded == str(Path.home() / "Documents" / "Scans")


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "bridge_destination.txt"

    save_bridge_destination(path, "/tmp/dossiary-scans")
    loaded = load_bridge_destination(path)

    assert loaded == "/tmp/dossiary-scans"


def test_load_falls_back_to_default_for_empty_file(tmp_path):
    path = tmp_path / "bridge_destination.txt"
    path.write_text("   \n")

    loaded = load_bridge_destination(path)

    assert loaded == str(Path.home() / "Documents" / "Scans")


def test_default_bridge_destination_path_lives_next_to_profiles_json():
    path = default_bridge_destination_path()

    assert path.parent == Path.home() / "Library" / "Application Support" / "scanix500"
    assert path.name == "bridge_destination.txt"
