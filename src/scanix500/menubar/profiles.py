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


def _seed_default(path: Path) -> list[Profile]:
    seeded = [_default_profile()]
    save_profiles(path, seeded)
    return seeded


def load_profiles(path: Path) -> list[Profile]:
    if not path.exists():
        return _seed_default(path)
    try:
        data = json.loads(path.read_text())
        return [Profile(**entry) for entry in data]
    except (json.JSONDecodeError, TypeError, KeyError):
        # A corrupt or schema-drifted profiles.json must not crash the app
        # before the menu bar icon ever appears (under launchd there is no
        # terminal to show the traceback). Recover with a fresh default.
        return _seed_default(path)


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
