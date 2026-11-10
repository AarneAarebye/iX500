from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from scanix500.i18n import t
from scanix500.scanners import IX500_ID, KNOWN_SCANNERS, get_scanner


@dataclass
class Profile:
    name: str
    destination: str
    skip_blank_filter: bool = False
    skip_ocr: bool = False
    split_on_blank: bool = False
    duplex: bool = True
    color_mode: str = "color"
    resolution: int = 300
    keep_blank_pages: bool = False
    scanner: str = IX500_ID
    format: str = "pdf"
    area: str = "a4"


def scanner_choices() -> list[tuple[str, str]]:
    """(id, name) of every scanner a profile can use, the iX500 first."""
    return [(s.id, s.name) for s in KNOWN_SCANNERS]


def profile_for_scanner(profile: Profile, scanner_id: str) -> Profile:
    """A copy of `profile` set to `scanner_id`. A flatbed gets single pages
    (no duplex, no separator sheets) and only values it offers; the iX500
    case changes nothing but the scanner."""
    known = get_scanner(scanner_id)
    if known is None:
        raise ValueError(f"Unknown scanner {scanner_id!r}")
    result = replace(profile, scanner=scanner_id)
    if "feeder" in known.entry.get("sources", []):
        return result
    entry = known.entry
    if result.resolution not in entry.get("resolutions", []):
        result.resolution = 300
    if result.color_mode not in entry.get("colorModes", []):
        result.color_mode = "color"
    if result.format not in entry.get("formats", []):
        result.format = "pdf"
    if result.area not in entry.get("areas", []):
        result.area = "a4"
    result.duplex = False
    result.split_on_blank = False
    return result


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
        raise ValueError(t("A profile named {name!r} already exists", name=profile.name))
    return [*profiles, profile]


def replace_profile(profiles: list[Profile], name: str, updated: Profile) -> list[Profile]:
    if not any(p.name == name for p in profiles):
        raise ValueError(t("No profile named {name!r}", name=name))
    if updated.name != name and any(p.name == updated.name for p in profiles):
        raise ValueError(t("A profile named {name!r} already exists", name=updated.name))
    return [updated if p.name == name else p for p in profiles]


def delete_profile(profiles: list[Profile], name: str) -> list[Profile]:
    if not any(p.name == name for p in profiles):
        raise ValueError(t("No profile named {name!r}", name=name))
    if len(profiles) == 1:
        raise ValueError(t("Cannot delete the last remaining profile"))
    return [p for p in profiles if p.name != name]
