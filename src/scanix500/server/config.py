"""scanix500-server's settings: /etc/scanix500/server.conf (an INI file
with a [server] section) and the state folder, /var/lib/scanix500."""

from __future__ import annotations

import configparser
import os
import socket
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REMOTE_PORT = 8767
DEFAULT_CONFIG = Path("/etc/scanix500/server.conf")
DEFAULT_STATE_DIR = Path("/var/lib/scanix500")


@dataclass(frozen=True)
class ServerConfig:
    port: int
    name: str
    state_dir: Path

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


def load_config(path: Path | None = None) -> ServerConfig:
    path = path or Path(os.environ.get("SCANIX500_CONFIG", str(DEFAULT_CONFIG)))
    parser = configparser.ConfigParser()
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
    return ServerConfig(port=port, name=name, state_dir=Path(state))
