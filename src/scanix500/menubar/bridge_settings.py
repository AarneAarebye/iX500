from __future__ import annotations

import os
import tempfile
from pathlib import Path

_DEFAULT_DESTINATION = str(Path.home() / "Documents" / "Scans")


def default_bridge_destination_path() -> Path:
    return Path.home() / "Library" / "Application Support" / "scanix500" / "bridge_destination.txt"


def load_bridge_destination(path: Path) -> str:
    """The folder a Dossiary-triggered (bridge) scan's safety-net copy is
    written to -- a single small value, not a Profile, since there's no
    more named-profile concept on this path (see the 2026-09-16
    parameterized-scan design spec). Falls back to the same
    ~/Documents/Scans default the "Default"/"Hardware Button" profiles
    already use, both when the file doesn't exist yet and when it exists
    but is empty/whitespace-only."""
    if not path.exists():
        return _DEFAULT_DESTINATION
    value = path.read_text().strip()
    return value or _DEFAULT_DESTINATION


def save_bridge_destination(path: Path, destination: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically so an interrupted write can't leave a truncated
    # file for the next load to trip over -- same pattern
    # profiles.save_profiles() already uses.
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(destination)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
