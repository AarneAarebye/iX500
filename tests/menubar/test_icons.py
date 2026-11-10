import importlib.util
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
ICON_DIR = ROOT / "src" / "scanix500" / "menubar" / "icons"
NAMES = ("idle", "scanning", "collecting")


def _generator():
    spec = importlib.util.spec_from_file_location("make_menubar_icons", ROOT / "scripts" / "make_menubar_icons.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_icons_are_36px_black_template_images_at_144_dpi():
    for name in NAMES:
        path = ICON_DIR / f"{name}.png"
        assert path.is_file(), name
        im = Image.open(path)
        assert im.size == (36, 36) and im.mode == "RGBA"
        assert round(im.info["dpi"][0]) == 144 and round(im.info["dpi"][1]) == 144
        alphas = set()
        for r, g, b, a in im.getdata():
            assert (r, g, b) == (0, 0, 0)
            alphas.add(a)
        assert 0 in alphas and 255 in alphas


def test_idle_and_scanning_differ():
    assert (ICON_DIR / "idle.png").read_bytes() != (ICON_DIR / "scanning.png").read_bytes()


def test_generator_reproduces_the_committed_files(tmp_path):
    _generator().main(tmp_path)
    for name in NAMES:
        made, kept = Image.open(tmp_path / f"{name}.png").convert("RGBA"), Image.open(ICON_DIR / f"{name}.png").convert("RGBA")
        assert made.size == kept.size and list(made.getdata()) == list(kept.getdata())
