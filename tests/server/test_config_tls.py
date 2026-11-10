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


def test_collecting_defaults_and_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANIX500_STATE_DIR", str(tmp_path / "state"))
    cfg = load_config(tmp_path / "missing.conf")
    assert (cfg.collect_timeout_minutes, cfg.button_custom_mode, cfg.button_custom_resolution) == (10, "gray", 300)
    assert cfg.collection_path == tmp_path / "state" / "collection"
    assert cfg.button_scans_path == tmp_path / "state" / "button-scans"


def test_collecting_values_are_read(tmp_path):
    conf = tmp_path / "server.conf"
    conf.write_text("[server]\ncollect_timeout_minutes = 3\nbutton_custom_mode = bw\nbutton_custom_resolution = 600\n")
    cfg = load_config(conf)
    assert (cfg.collect_timeout_minutes, cfg.button_custom_mode, cfg.button_custom_resolution) == (3, "bw", 600)


@pytest.mark.parametrize("key, value", [
    ("collect_timeout_minutes", "0"), ("collect_timeout_minutes", "ten"),
    ("button_custom_mode", "sepia"), ("button_custom_resolution", "200"),
    ("button_custom_resolution", "fast"),
])
def test_bad_collecting_values_name_the_key_and_file(tmp_path, key, value):
    conf = tmp_path / "server.conf"
    conf.write_text(f"[server]\n{key} = {value}\n")
    with pytest.raises(ValueError, match=key) as err:
        load_config(conf)
    assert str(conf) in str(err.value)


@pytest.mark.parametrize("value", ["1200", "2400"])
def test_button_custom_resolution_over_the_pixel_limit_is_refused(tmp_path, value):
    conf = tmp_path / "s.conf"
    conf.write_text(f"[server]\nbutton_custom_resolution = {value}\n")
    with pytest.raises(ValueError, match="button_custom_resolution must be one of 150, 300, 600"):
        load_config(conf)


def test_button_feedback_command_default_and_value(tmp_path):
    conf = tmp_path / "server.conf"
    conf.write_text("[server]\nport = 9443\n")
    assert load_config(conf).button_feedback_command == ""
    conf.write_text('[server]\nbutton_feedback_command = /usr/bin/say -v "Anna K"\n')
    assert load_config(conf).button_feedback_command == '/usr/bin/say -v "Anna K"'


def test_button_feedback_command_with_unbalanced_quote_is_refused(tmp_path):
    conf = tmp_path / "server.conf"
    conf.write_text("[server]\nbutton_feedback_command = say \"x\n")
    with pytest.raises(ValueError, match="button_feedback_command"):
        load_config(conf)


def test_percent_in_the_feedback_command_is_kept_literally(tmp_path):
    conf = tmp_path / "server.conf"
    conf.write_text("[server]\nbutton_feedback_command = /bin/sh -c 'date +%T'\n")
    assert load_config(conf).button_feedback_command == "/bin/sh -c 'date +%T'"
