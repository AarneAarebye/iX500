from PIL import Image, ImageDraw

from scanix500.blank_filter import filter_pages, is_blank
from scanix500.capture import PagePair


def _blank_image():
    return Image.new("L", (100, 100), color=255)


def _content_image():
    im = Image.new("L", (100, 100), color=255)
    draw = ImageDraw.Draw(im)
    draw.rectangle([10, 10, 90, 90], fill=0)
    return im


def test_is_blank_true_for_all_white_image():
    assert is_blank(_blank_image()) is True


def test_is_blank_false_for_image_with_content():
    assert is_blank(_content_image()) is False
