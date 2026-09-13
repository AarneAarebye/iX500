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
