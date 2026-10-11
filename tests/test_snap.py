from PIL import Image

from scanix500.snap import snap_to_standard_size

DPI = 300


def _px(mm: float) -> int:
    return round(mm / 25.4 * DPI)


def _page(w_mm: float, h_mm: float, mode: str = "L") -> Image.Image:
    im = Image.new(mode, (_px(w_mm), _px(h_mm)), 128 if mode == "L" else (128, 128, 128))
    im.info["dpi"] = (DPI, DPI)
    return im


def test_close_to_a4_becomes_exactly_a4():
    out = snap_to_standard_size(_page(207, 295))
    assert out.size == (_px(210), _px(297))
    assert out.info["dpi"] == (DPI, DPI)


def test_slightly_larger_than_a4_is_cropped_to_a4():
    assert snap_to_standard_size(_page(212, 299)).size == (_px(210), _px(297))


def test_letter_and_a5_and_landscape_a5():
    assert snap_to_standard_size(_page(214, 278)).size == (_px(215.9), _px(279.4))
    assert snap_to_standard_size(_page(148, 211)).size == (_px(148), _px(210))
    assert snap_to_standard_size(_page(211, 147)).size == (_px(210), _px(148))


def test_other_sizes_are_kept():
    receipt = _page(80, 200)
    assert snap_to_standard_size(receipt).size == receipt.size
    assert snap_to_standard_size(_page(200, 297)).size == (_px(200), _px(297))


def test_padding_is_white_and_centred():
    out = snap_to_standard_size(_page(206, 297, "RGB"))
    assert out.mode == "RGB"
    assert out.getpixel((0, 1000)) == (255, 255, 255)
    assert out.getpixel((out.width // 2, 1000)) == (128, 128, 128)


def test_one_bit_pages_are_padded_white():
    im = Image.new("1", (_px(206), _px(297)), 0)
    im.info["dpi"] = (DPI, DPI)
    out = snap_to_standard_size(im)
    assert out.size == (_px(210), _px(297)) and out.getpixel((0, 1000)) == 255


def test_other_modes_and_missing_dpi():
    rgba = Image.new("RGBA", (_px(206), _px(297)))
    rgba.info["dpi"] = (DPI, DPI)
    assert snap_to_standard_size(rgba).size == rgba.size
    no_dpi = Image.new("L", (_px(206), _px(297)))  # assumed 300 dpi
    assert snap_to_standard_size(no_dpi).size == (_px(210), _px(297))
