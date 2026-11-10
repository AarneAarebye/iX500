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
