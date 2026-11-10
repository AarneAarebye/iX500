import re
from pathlib import Path

DEB = Path(__file__).resolve().parent.parent / "packaging" / "debian"
DLL = "/etc/scanix500/sane.d/dll.conf"


def test_dll_conf_lists_exactly_the_needed_backends():
    lines = [l.strip() for l in (DEB / "sane.d" / "dll.conf").read_text().splitlines()]
    backends = [l for l in lines if l and not l.startswith("#")]
    assert backends == ["fujitsu", "epjitsu", "pixma"]
    assert lines[0].startswith("#")


def test_unit_sets_sane_config_dir_with_trailing_colon():
    unit = (DEB / "scanix500-server.service").read_text()
    assert re.search(r"^Environment=SANE_CONFIG_DIR=/etc/scanix500/sane\.d:$", unit, re.M)


def test_dll_conf_is_a_conffile():
    assert DLL in (DEB / "conffiles").read_text().split()


def test_wrappers_export_sane_config_dir_only_when_unset():
    for name in ("scanix500.sh", "scanix500-server.sh"):
        text = (DEB / name).read_text()
        assert ': "${SANE_CONFIG_DIR:=/etc/scanix500/sane.d:}"' in text, name
        assert "export SANE_CONFIG_DIR" in text, name
        assert "unset SANE_CONFIG_DIR" not in text and "env -i" not in text


def test_build_script_installs_the_file():
    text = (DEB / "build-deb.sh").read_text()
    assert "sane.d/dll.conf" in text and 'etc/scanix500/sane.d"' in text


def test_scan_subprocess_inherits_the_environment():
    src = (Path(__file__).resolve().parent.parent / "src/scanix500/menubar/runner.py").read_text()
    call = re.search(r"subprocess\.run\([^\n]*\)", src).group(0)
    assert "env=" not in call


# --- scanix500-beep (sound from the Pi) ---

import os
import stat
import subprocess
import wave

BEEP = DEB / "scanix500-beep.sh"
SOUNDS = DEB / "sounds"


def test_beep_script_is_executable_and_parses():
    assert BEEP.is_file() and BEEP.stat().st_mode & stat.S_IXUSR
    subprocess.run(["sh", "-n", str(BEEP)], check=True)


def test_build_script_installs_beep_and_sounds():
    text = (DEB / "build-deb.sh").read_text()
    assert "usr/bin/scanix500-beep" in text
    assert "usr/share/scanix500/sounds" in text
    assert "press.wav" in text and "finish.wav" in text


def test_postinst_adds_service_user_to_audio():
    assert "adduser scanix500 audio" in (DEB / "postinst").read_text()


def test_control_depends_on_alsa_utils():
    depends = re.search(r"^Depends:.*$", (DEB / "control.in").read_text(), re.M).group(0)
    assert "alsa-utils" in depends


def test_server_conf_documents_feedback_command():
    assert "# button_feedback_command = /usr/bin/scanix500-beep" in (DEB / "server.conf").read_text()


def test_wav_files_are_valid_and_differ():
    for name in ("press.wav", "finish.wav"):
        with wave.open(str(SOUNDS / name)) as w:
            assert w.getnchannels() == 1 and w.getsampwidth() == 2
            assert w.getframerate() in (22050, 44100)
            assert 0.05 <= w.getnframes() / w.getframerate() <= 1.0
    assert (SOUNDS / "press.wav").read_bytes() != (SOUNDS / "finish.wav").read_bytes()


def _run_beep(tmp_path, *args, aplay_body='[ "$1" = -l ] && exit 0; printf "%s\\n" "$*" >> "$LOG"', with_aplay=True):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "aplay.log"
    if with_aplay:
        fake = bindir / "aplay"
        fake.write_text(f"#!/bin/sh\n{aplay_body}\n")
        fake.chmod(0o755)
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin" if with_aplay else str(bindir),
        "LOG": str(log),
        "SCANIX500_SOUNDS": str(SOUNDS),
    }
    r = subprocess.run(["/bin/sh", str(BEEP), *args], env=env, capture_output=True)
    return r, (log.read_text().splitlines() if log.exists() else [])


def test_beep_plays_press_for_buttons(tmp_path):
    r, calls = _run_beep(tmp_path, "color")
    assert r.returncode == 0
    assert calls == [f"-q {SOUNDS}/press.wav"]


def test_beep_plays_finish(tmp_path):
    _, calls = _run_beep(tmp_path, "finish")
    assert calls == [f"-q {SOUNDS}/finish.wav"]


def test_beep_passes_device(tmp_path):
    _, calls = _run_beep(tmp_path, "-D", "plughw:1,0", "bw")
    assert calls == [f"-q -D plughw:1,0 {SOUNDS}/press.wav"]


def test_beep_unknown_button_plays_press(tmp_path):
    _, calls = _run_beep(tmp_path, "banana")
    assert calls == [f"-q {SOUNDS}/press.wav"]


def test_beep_never_fails(tmp_path):
    r, _ = _run_beep(tmp_path, "color", with_aplay=False)
    assert r.returncode == 0
    r, _ = _run_beep(tmp_path, "color", aplay_body="exit 3")
    assert r.returncode == 0


def test_beep_shows_aplay_errors_but_exits_zero(tmp_path):
    r, _ = _run_beep(tmp_path, "color", aplay_body='[ "$1" = -l ] && exit 0; echo "no such device" >&2; exit 1')
    assert r.returncode == 0
    assert b"no such device" in r.stderr


def test_beep_dash_alone_exits_zero(tmp_path):
    r, _ = _run_beep(tmp_path, "-D")
    assert r.returncode == 0


def test_beep_autodetects_headphones(tmp_path):
    body = '[ "$1" = -l ] && { echo "card 2: Headphones [bcm2835 Headphones]"; exit 0; }; printf "%s\\n" "$*" >> "$LOG"'
    _, calls = _run_beep(tmp_path, "color", aplay_body=body)
    assert calls == [f"-q -D plughw:CARD=Headphones {SOUNDS}/press.wav"]


def test_beep_env_device_wins(tmp_path):
    body = '[ "$1" = -l ] && { echo Headphones; exit 0; }; printf "%s\\n" "$*" >> "$LOG"'
    bindir = tmp_path / "bin"; bindir.mkdir()
    fake = bindir / "aplay"; fake.write_text(f"#!/bin/sh\n{body}\n"); fake.chmod(0o755)
    log = tmp_path / "l"
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "LOG": str(log), "SCANIX500_SOUNDS": str(SOUNDS),
           "SCANIX500_BEEP_DEVICE": "plughw:3,0"}
    subprocess.run(["/bin/sh", str(BEEP), "bw"], env=env, check=True)
    assert log.read_text().splitlines() == [f"-q -D plughw:3,0 {SOUNDS}/press.wav"]
