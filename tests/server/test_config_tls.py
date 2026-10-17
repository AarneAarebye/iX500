import ssl
import stat

import pytest

from scanix500.server.config import DEFAULT_REMOTE_PORT, load_config
from scanix500.server.tls import certificate_sha256, display_fingerprint, generate_certificate, server_context


def test_defaults_without_a_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANIX500_STATE_DIR", str(tmp_path / "state"))
    cfg = load_config(tmp_path / "missing.conf")
    assert cfg.port == DEFAULT_REMOTE_PORT == 8767
    assert cfg.name  # the host name
    assert cfg.cert_path == tmp_path / "state" / "tls" / "cert.pem"
    assert cfg.window_path == tmp_path / "state" / "pairing-window.json"


def test_config_file_values(tmp_path, monkeypatch):
    monkeypatch.delenv("SCANIX500_STATE_DIR", raising=False)
    conf = tmp_path / "server.conf"
    conf.write_text("[server]\nport = 9443\nname = Hallway scanner\nstate_dir = %s\n" % (tmp_path / "s"))
    cfg = load_config(conf)
    assert (cfg.port, cfg.name, cfg.state_dir) == (9443, "Hallway scanner", tmp_path / "s")


def test_bad_port_is_reported(tmp_path):
    conf = tmp_path / "server.conf"
    conf.write_text("[server]\nport = seventy\n")
    with pytest.raises(ValueError, match="port"):
        load_config(conf)


def test_certificate_is_made_once_and_loads(tmp_path):
    tls = tmp_path / "tls"
    assert generate_certificate(tls, "raspberrypi") is True
    assert generate_certificate(tls, "raspberrypi") is False
    assert stat.S_IMODE((tls / "key.pem").stat().st_mode) == 0o600
    digest = certificate_sha256(tls / "cert.pem")
    assert len(digest) == 64 and digest == digest.upper()
    shown = display_fingerprint(digest)
    assert shown.count("-") == 7 and shown.replace("-", "") == digest[:32]
    assert isinstance(server_context(tls / "cert.pem", tls / "key.pem"), ssl.SSLContext)
