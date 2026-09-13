from __future__ import annotations

import json
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


def load_profiles(path: Path) -> list[Profile]:
    if not path.exists():
        seeded = [_default_profile()]
        save_profiles(path, seeded)
        return seeded
    data = json.loads(path.read_text())
    return [Profile(**entry) for entry in data]


def save_profiles(path: Path, profiles: list[Profile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(p) for p in profiles], indent=2))


def add_profile(profiles: list[Profile], profile: Profile) -> list[Profile]:
    return [*profiles, profile]


def replace_profile(profiles: list[Profile], name: str, updated: Profile) -> list[Profile]:
    if not any(p.name == name for p in profiles):
        raise ValueError(f"No profile named {name!r}")
    return [updated if p.name == name else p for p in profiles]


def delete_profile(profiles: list[Profile], name: str) -> list[Profile]:
    if not any(p.name == name for p in profiles):
        raise ValueError(f"No profile named {name!r}")
    if len(profiles) == 1:
        raise ValueError("Cannot delete the last remaining profile")
    return [p for p in profiles if p.name != name]
