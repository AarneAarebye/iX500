"""scanix500-server's settings: /etc/scanix500/server.conf (an INI file
with a [server] section) and the state folder, /var/lib/scanix500."""

from __future__ import annotations

import configparser
import os
import shlex
import socket
from dataclasses import dataclass
from pathlib import Path

from scanix500.scanners import MAX_PIXELS, pixel_count

_RESOLUTIONS = (150, 300, 600, 1200)  # only those an A4 page allows within MAX_PIXELS are accepted
DEFAULT_REMOTE_PORT = 8767
DEFAULT_CONFIG = Path("/etc/scanix500/server.conf")
DEFAULT_STATE_DIR = Path("/var/lib/scanix500")


@dataclass(frozen=True)
class ServerConfig:
    port: int
    name: str
    state_dir: Path
    collect_timeout_minutes: int = 10
    button_custom_mode: str = "gray"
    button_custom_resolution: int = 300
    button_feedback_command: str = ""

    @property
    def tls_dir(self) -> Path: return self.state_dir / "tls"
    @property
    def cert_path(self) -> Path: return self.tls_dir / "cert.pem"
    @property
    def key_path(self) -> Path: return self.tls_dir / "key.pem"
    @property
    def pairing_path(self) -> Path: return self.state_dir / "paired_clients.json"
    @property
    def window_path(self) -> Path: return self.state_dir / "pairing-window.json"
    @property
    def ocr_jobs_path(self) -> Path: return self.state_dir / "ocr-jobs"
    @property
    def scans_path(self) -> Path: return self.state_dir / "scans"
    @property
    def collection_path(self) -> Path: return self.state_dir / "collection"
    @property
    def button_scans_path(self) -> Path: return self.state_dir / "button-scans"


def load_config(path: Path | None = None) -> ServerConfig:
    path = path or Path(os.environ.get("SCANIX500_CONFIG", str(DEFAULT_CONFIG)))
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)  # a missing file just means the defaults
    section = parser["server"] if parser.has_section("server") else {}
    raw_port = section.get("port", str(DEFAULT_REMOTE_PORT))
    try:
        port = int(raw_port)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError(f"{path}: port must be a number from 1 to 65535, not “{raw_port}”.") from None
    name = (section.get("name") or "").strip() or socket.gethostname().split(".")[0]
    state = os.environ.get("SCANIX500_STATE_DIR") or section.get("state_dir") or str(DEFAULT_STATE_DIR)
    raw_timeout = section.get("collect_timeout_minutes", "10")
    try:
        timeout = int(raw_timeout)
        if timeout < 1:
            raise ValueError
    except ValueError:
        raise ValueError(f"{path}: collect_timeout_minutes must be a whole number from 1 up, not “{raw_timeout}”.") from None
    mode = section.get("button_custom_mode", "gray").strip()
    if mode not in ("gray", "color", "bw"):
        raise ValueError(f"{path}: button_custom_mode must be gray, color or bw, not “{mode}”.")
    raw_res = section.get("button_custom_resolution", "300")
    try:
        resolution = int(raw_res)
        if resolution not in _RESOLUTIONS or pixel_count("a4", resolution) > MAX_PIXELS:
            raise ValueError
    except ValueError:
        allowed = ", ".join(str(r) for r in _RESOLUTIONS if pixel_count("a4", r) <= MAX_PIXELS)
        raise ValueError(f"{path}: button_custom_resolution must be one of {allowed}, not “{raw_res}”.") from None
    raw_cmd = section.get("button_feedback_command", "").strip()
    try:
        shlex.split(raw_cmd)
    except ValueError as exc:
        raise ValueError(f"{path}: button_feedback_command is not a valid command line: {exc}") from None
    return ServerConfig(port=port, name=name, state_dir=Path(state), collect_timeout_minutes=timeout,
                        button_custom_mode=mode, button_custom_resolution=resolution,
                        button_feedback_command=raw_cmd)
