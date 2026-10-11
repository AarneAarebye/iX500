from PIL import Image

from scanix500.bw import BW_THRESHOLD, to_black_and_white


def test_to_black_and_white_thresholds_gray_and_keeps_dpi():
    image = Image.new("L", (4, 1))
    image.putdata([0, BW_THRESHOLD - 1, BW_THRESHOLD, 255])
    image.info["dpi"] = (600, 600)
    out = to_black_and_white(image)
    assert out.mode == "1"
    assert [0 if v == 0 else 255 for v in out.getdata()] == [0, 0, 255, 255]
    assert out.info["dpi"] == (600, 600)


def test_to_black_and_white_accepts_color():
    image = Image.new("RGB", (2, 2), (250, 250, 250))
    assert to_black_and_white(image).mode == "1"
