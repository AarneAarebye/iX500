import json
import ssl
import subprocess
import sys
import threading
import urllib.request

import pytest

from scanix500.menubar.bridge import RemoteOptions, ScanBusyError
from scanix500.menubar.fakes import FakeBackend
from scanix500.menubar.pairing import SharedWindowPairingStore
from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult
from scanix500.server.button_pairing import ButtonPairing
from scanix500.server.config import ServerConfig
from scanix500.server.runtime import ButtonWatcher, ServerScanTrigger, start_remote_server
from scanix500.server.tls import certificate_sha256, generate_certificate, server_context

PROFILE = Profile(name="x", destination="/tmp")


def test_trigger_runs_one_scan_at_a_time():
    started, release = threading.Event(), threading.Event()
    def slow(profile):
        started.set(); release.wait(5)
        return ScanResult(True, False, "done", [])
    trigger = ServerScanTrigger(run=slow)
    t = threading.Thread(target=trigger.trigger, args=(PROFILE,)); t.start()
    started.wait(5)
    assert trigger.busy
    with pytest.raises(ScanBusyError):
        trigger.trigger(PROFILE)
    release.set(); t.join()
    assert not trigger.busy


def test_trigger_turns_a_crash_into_a_failed_result():
    def boom(profile): raise RuntimeError("scanner exploded")
    result = ServerScanTrigger(run=boom).trigger(PROFILE)
    assert not result.ok and "exploded" in result.message


def test_button_watcher_only_polls_while_a_request_waits():
    issued = []
    bp = ButtonPairing(lambda c: issued.append(c) or "tok")
    calls = []
    watcher = ButtonWatcher(bp, ServerScanTrigger(), resolve=lambda: "fujitsu:ix500",
                            pressed=lambda dev: calls.append(dev) or True)
    watcher.poll_once()
    assert calls == []  # nothing pending: the scanner isn't touched
    bp.request("Mac")
    watcher.poll_once()
    assert calls == ["fujitsu:ix500"] and issued == ["Mac"]


def test_server_answers_over_https_with_its_fingerprint(tmp_path):
    cfg = ServerConfig(port=0, name="raspberrypi", state_dir=tmp_path)
    generate_certificate(cfg.tls_dir, "raspberrypi")
    pairing = SharedWindowPairingStore(cfg.pairing_path, cfg.window_path)
    fp = certificate_sha256(cfg.cert_path)
    remote = RemoteOptions(fingerprint=fp, name=cfg.name, button_pairing=ButtonPairing(pairing.issue_token))
    httpd = start_remote_server(cfg, FakeBackend(), pairing, remote, server_context(cfg.cert_path, cfg.key_path))
    try:
        port = httpd.server_address[1]
        client = ssl.create_default_context(cafile=str(cfg.cert_path))
        client.check_hostname = False
        with urllib.request.urlopen(f"https://127.0.0.1:{port}/health", context=client) as resp:
            body = json.loads(resp.read())
        assert body["fingerprint"] == fp and body["remote"] is True
    finally:
        httpd.shutdown()


def test_server_package_never_imports_the_mac_app_libraries():
    code = ("import sys, scanix500.server.cli; "
            "print(any(m in sys.modules for m in ('rumps', 'AppKit', 'Foundation', 'objc')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"
