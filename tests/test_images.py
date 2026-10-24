import pytest
from PIL import Image

from scanix500.images import EXTENSIONS, save_image


def _page(mode="RGB", size=(40, 30)):
    fill = {"RGB": (255, 255, 255), "L": 255, "1": 1}[mode]
    return Image.new(mode, size, fill)


def test_extensions():
    assert EXTENSIONS == {"jpeg": "jpg", "png": "png"}


def test_a_jpeg_has_the_right_signature_size_and_dpi(tmp_path):
    path = tmp_path / "a.jpg"
    save_image(_page(), path, "jpeg", 600)
    assert path.read_bytes().startswith(b"\xff\xd8\xff")
    result = Image.open(path)
    assert (result.format, result.size, result.mode) == ("JPEG", (40, 30), "RGB")
    assert [round(v) for v in result.info["dpi"]] == [600, 600]


def test_a_gray_jpeg_stays_gray(tmp_path):
    path = tmp_path / "g.jpg"
    save_image(_page("L"), path, "jpeg", 300)
    assert Image.open(path).mode == "L"


def test_a_one_bit_page_saved_as_jpeg_becomes_gray(tmp_path):
    path = tmp_path / "b.jpg"
    save_image(_page("1"), path, "jpeg", 300)
    assert Image.open(path).mode == "L"


def test_a_png_keeps_its_mode_size_and_dpi(tmp_path):
    path = tmp_path / "a.png"
    save_image(_page(), path, "png", 1200)
    assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    result = Image.open(path)
    assert (result.format, result.size, result.mode) == ("PNG", (40, 30), "RGB")
    assert [round(v) for v in result.info["dpi"]] == [1200, 1200]


def test_a_black_and_white_png_is_one_bit(tmp_path):
    path = tmp_path / "bw.png"
    save_image(_page("1"), path, "png", 600)
    assert Image.open(path).mode == "1"


def test_an_unknown_format_is_refused(tmp_path):
    with pytest.raises(ValueError):
        save_image(_page(), tmp_path / "x.tif", "tiff", 300)
    assert not (tmp_path / "x.tif").exists()
