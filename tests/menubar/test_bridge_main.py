import pytest

from scanix500.menubar.bridge_main import parse_args


def test_fake_scanners_flag_and_port():
    args = parse_args(["--fake-scanners", "--port", "8799"])
    assert args.fake_scanners and args.port == 8799


def test_real_mode_is_refused():
    with pytest.raises(SystemExit):
        parse_args([])
