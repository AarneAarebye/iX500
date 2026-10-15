"""install-app.sh's failure paths, run with fake brew/launchctl and a temp
install location so nothing real is touched. The success path is checked by
hand (it builds a real venv and loads a real LaunchAgent)."""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "install-app.sh"


def _fake(bin_dir: Path, name: str, body: str) -> Path:
    path = bin_dir / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


def _run(tmp_path: Path, args: list[str], missing_formula: str = ""):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "launchctl-calls"
    launchctl = _fake(bin_dir, "launchctl", f'echo "$@" >> "{calls}"\n')
    brew = _fake(
        bin_dir,
        "brew",
        f'if [ "$1" = list ] && [ "$3" = "{missing_formula}" ]; then exit 1; fi\n'
        'if [ "$1" = --prefix ]; then echo /nonexistent; fi\n'
        "exit 0\n",
    )
    env = dict(
        os.environ,
        SCANIX500_INSTALL_DIR=str(tmp_path / "install"),
        SCANIX500_AGENT_DIR=str(tmp_path / "agents"),
        SCANIX500_LOG=str(tmp_path / "logs" / "scanix500.log"),
        SCANIX500_BIN_LINK=str(tmp_path / "link" / "scanix500"),
        LAUNCHCTL=str(launchctl),
        BREW=str(brew),
    )
    result = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)
    return result, calls


def _assert_nothing_changed(tmp_path: Path, calls: Path):
    assert not (tmp_path / "install").exists()
    assert not (tmp_path / "agents").exists()
    assert not (tmp_path / "logs").exists()
    assert not (tmp_path / "link").exists()
    assert not calls.exists(), "launchctl must not be called"


def test_no_argument_prints_usage(tmp_path):
    result, calls = _run(tmp_path, [])
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    _assert_nothing_changed(tmp_path, calls)


def test_missing_prerequisite_stops_before_anything_changes(tmp_path):
    result, calls = _run(tmp_path, ["HEAD"], missing_formula="ocrmypdf")
    assert result.returncode == 1
    assert "install-app: ocrmypdf is missing" in result.stderr
    assert "brew install sane-backends ocrmypdf tesseract tesseract-lang" in result.stderr
    _assert_nothing_changed(tmp_path, calls)


def test_unknown_ref_stops_before_anything_changes(tmp_path):
    result, calls = _run(tmp_path, ["v0.0.0-does-not-exist"])
    assert result.returncode == 1
    assert "install-app: no such tag or commit: v0.0.0-does-not-exist" in result.stderr
    _assert_nothing_changed(tmp_path, calls)
