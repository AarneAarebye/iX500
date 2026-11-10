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


def test_avahi_service_uses_the_configured_port_and_name(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "s.conf"
    conf.write_text("[server]\nport = 9443\nname = Hallway & co\n")
    monkeypatch.setenv("SCANIX500_CONFIG", str(conf))
    monkeypatch.setenv("SCANIX500_STATE_DIR", str(tmp_path / "state"))
    assert main(["avahi-service"]) == 0
    xml = capsys.readouterr().out
    assert "<type>_dossiary-scan._tcp</type>" in xml and "<port>9443</port>" in xml
    assert "Hallway &amp; co" in xml and "<txt-record>proto=2</txt-record>" in xml


def test_status_without_permission_to_read_the_state(state, capsys, monkeypatch):
    monkeypatch.setattr("scanix500.server.cli.resolve_device_name", lambda: "fujitsu:ScanSnap iX500:1")
    main(["init"]); capsys.readouterr()
    (state / "paired_clients.json").write_text("{}")
    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")
    # What a normal user meets on the Pi: /var/lib/scanix500 is 0750 scanix500.
    monkeypatch.setattr("scanix500.server.cli._fingerprint_line", denied)
    monkeypatch.setattr("scanix500.server.cli._paired_count", denied)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Fingerprint: (run with sudo" in out and "Paired computers: (run with sudo" in out
    assert "Scanner: fujitsu:ScanSnap iX500:1" in out


def _pdf(path):
    from PIL import Image
    Image.new("RGB", (60, 80)).save(path, "PDF")
    return path


def test_status_shows_button_scans(state, capsys, monkeypatch):
    from scanix500.collection import CollectionSettings, ScanCollection
    from scanix500.server.button_scans import ButtonScanStore
    monkeypatch.setattr("scanix500.server.cli.resolve_device_name", lambda: None)
    main(["status"]); out = capsys.readouterr().out
    assert "Button scans: nothing collected" in out and "Waiting for pickup: 0" in out
    c = ScanCollection(state / "collection")
    c.add_page(_pdf(state / "a.pdf"), CollectionSettings(False, ["eng"], "sane:canoscan-8800f"))
    c.add_page(_pdf(state / "b.pdf"), CollectionSettings(False, ["eng"], "sane:canoscan-8800f"))
    ButtonScanStore(state / "button-scans").add(_pdf(state / "c.pdf"), "scan_x.pdf", 1, skip_ocr=True, languages=[])
    main(["status"]); out = capsys.readouterr().out
    assert "Button scans: collecting 2 pages (closes in 10 min)" in out and "Waiting for pickup: 1" in out


def test_button_scans_list_and_discard_open(state, capsys, monkeypatch):
    from scanix500.collection import CollectionSettings, ScanCollection
    from scanix500.server.button_scans import ButtonScanStore
    ButtonScanStore(state / "button-scans").add(_pdf(state / "c.pdf"), "scan_x.pdf", 3, skip_ocr=True, languages=[])
    assert main(["button-scans", "list"]) == 0
    assert "scan_x.pdf" in capsys.readouterr().out
    c = ScanCollection(state / "collection")
    c.add_page(_pdf(state / "a.pdf"), CollectionSettings(False, ["eng"], "sane:canoscan-8800f"))
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    assert main(["button-scans", "discard-open"]) == 1
    assert ScanCollection(state / "collection").page_count == 1
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    assert main(["button-scans", "discard-open"]) == 0
    assert ScanCollection(state / "collection").page_count == 0


def test_fake_run_does_not_start_the_collector(state, monkeypatch):
    import threading
    started = []
    monkeypatch.setattr("scanix500.server.cli.ButtonCollector.start", lambda self: started.append(1))
    main(["init"])
    monkeypatch.setattr(threading.Event, "wait", lambda self, *a: True)
    assert main(["run", "--fake-scanners"]) == 0
    assert started == []


def _snapshot(folder):
    return sorted((str(p), p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None) for p in folder.rglob("*"))


def test_status_and_list_never_write(state, capsys, monkeypatch):
    monkeypatch.setattr("scanix500.server.cli.resolve_device_name", lambda: None)
    (state / "collection").mkdir(parents=True)
    (state / "collection" / "page-001.pdf").write_bytes(b"nope")  # damaged: a ScanCollection would move it
    (state / "button-scans").mkdir()
    before = _snapshot(state)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "collecting 1 pages" in out
    assert main(["button-scans", "list"]) == 0
    assert _snapshot(state) == before


def test_status_and_button_scans_without_permission(state, capsys, monkeypatch):
    import os
    monkeypatch.setattr("scanix500.server.cli.resolve_device_name", lambda: None)
    def denied(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr("scanix500.server.cli._fingerprint_line", denied)
    monkeypatch.setattr("scanix500.server.cli._paired_count", denied)
    monkeypatch.setattr(os, "scandir", denied)
    assert main(["status"]) == 0
    assert "Button scans: (run with sudo" in capsys.readouterr().out
    assert main(["button-scans", "list"]) == 1
    assert "run with sudo" in capsys.readouterr().err
