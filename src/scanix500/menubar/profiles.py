from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Profile:
    name: str
    destination: str
    skip_blank_filter: bool = False
    skip_ocr: bool = False
    split_on_blank: bool = False


def default_profiles_path() -> Path:
    return Path.home() / "Library" / "Application Support" / "scanix500" / "profiles.json"


def _default_profile() -> Profile:
    return Profile(
        name="Default",
        destination=str(Path.home() / "Documents" / "Scans"),
    )


HARDWARE_BUTTON_PROFILE_NAME = "Hardware Button"


def _hardware_button_profile() -> Profile:
    return Profile(
        name=HARDWARE_BUTTON_PROFILE_NAME,
        destination=str(Path.home() / "Documents" / "Scans"),
    )


def _seed_default(path: Path) -> list[Profile]:
    seeded = [_default_profile(), _hardware_button_profile()]
    save_profiles(path, seeded)
    return seeded


def _hardware_button_marker_path(profiles_path: Path) -> Path:
    """Marks that the one-time Hardware Button migration has already run.

    Kept as a separate file rather than a field in profiles.json so
    save_profiles' bare-array format stays untouched — ordinary
    Add/Edit/Delete saves are unaffected.
    """
    return profiles_path.parent / ".hardware_button_seeded"


def load_profiles(path: Path) -> list[Profile]:
    marker = _hardware_button_marker_path(path)
    if not path.exists():
        seeded = _seed_default(path)
        marker.touch()
        return seeded
    try:
        data = json.loads(path.read_text())
        profiles = [Profile(**entry) for entry in data]
    except (json.JSONDecodeError, TypeError, KeyError):
        # A corrupt or schema-drifted profiles.json must not crash the app
        # before the menu bar icon ever appears (under launchd there is no
        # terminal to show the traceback). Recover with a fresh default.
        seeded = _seed_default(path)
        marker.touch()
        return seeded

    if not marker.exists():
        # One-time migration for installations that predate the Hardware
        # Button feature. After this runs once, a deliberate deletion of
        # the profile is permanent — we never re-add it again.
        if not any(p.name == HARDWARE_BUTTON_PROFILE_NAME for p in profiles):
            profiles = [*profiles, _hardware_button_profile()]
            save_profiles(path, profiles)
        marker.touch()
    return profiles


def find_profile(profiles: list[Profile], name: str) -> Profile | None:
    for profile in profiles:
        if profile.name == name:
            return profile
    return None


def save_profiles(path: Path, profiles: list[Profile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([asdict(p) for p in profiles], indent=2)
    # Write atomically so an interrupted write can't leave a truncated file
    # for the next load to trip over.
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def add_profile(profiles: list[Profile], profile: Profile) -> list[Profile]:
    if any(p.name == profile.name for p in profiles):
        raise ValueError(f"A profile named {profile.name!r} already exists")
    return [*profiles, profile]


def replace_profile(profiles: list[Profile], name: str, updated: Profile) -> list[Profile]:
    if not any(p.name == name for p in profiles):
        raise ValueError(f"No profile named {name!r}")
    if updated.name != name and any(p.name == updated.name for p in profiles):
        raise ValueError(f"A profile named {updated.name!r} already exists")
    return [updated if p.name == name else p for p in profiles]


def delete_profile(profiles: list[Profile], name: str) -> list[Profile]:
    if not any(p.name == name for p in profiles):
        raise ValueError(f"No profile named {name!r}")
    if len(profiles) == 1:
        raise ValueError("Cannot delete the last remaining profile")
    return [p for p in profiles if p.name != name]
