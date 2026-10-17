import pytest

from scanix500.server.cli import main


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANIX500_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SCANIX500_CONFIG", str(tmp_path / "none.conf"))
    return tmp_path / "state"


def test_init_makes_the_certificate_once(state, capsys):
    assert main(["init"]) == 0
    first = capsys.readouterr().out
    assert "Fingerprint:" in first and (state / "tls" / "cert.pem").exists()
    assert main(["init"]) == 0
    assert "already exists" in capsys.readouterr().out


def test_run_refuses_to_start_without_a_certificate(state, capsys):
    assert main(["run", "--fake-scanners"]) == 1
    assert "scanix500-server init" in capsys.readouterr().err


def test_pair_prints_a_code_and_the_fingerprint(state, capsys):
    main(["init"]); capsys.readouterr()
    assert main(["pair"]) == 0
    out = capsys.readouterr().out
    assert "Pairing code:" in out and "5 minutes" in out and "Fingerprint:" in out
    assert (state / "pairing-window.json").exists()


def test_clients_list_and_revoke(state, capsys):
    from scanix500.menubar.pairing import SharedWindowPairingStore
    store = SharedWindowPairingStore(state / "paired_clients.json", state / "pairing-window.json")
    store.issue_token("Office iMac")
    assert main(["clients"]) == 0
    assert "Office iMac" in capsys.readouterr().out
    assert main(["clients", "revoke", "Office iMac"]) == 0
    assert main(["clients", "revoke", "Office iMac"]) == 1
    assert "No paired computer" in capsys.readouterr().err


def test_status(state, capsys, monkeypatch):
    monkeypatch.setattr("scanix500.server.cli.resolve_device_name", lambda: None)
    main(["init"]); capsys.readouterr()
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Port: 8767" in out and "Scanner: not found" in out and "Paired computers: 0" in out
