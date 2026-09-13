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


def test_filter_pages_drops_blank_backside_keeps_front():
    pages = [PagePair(_content_image(), _blank_image())]

    partitions = filter_pages(pages)

    assert partitions == [[pages[0].front]]


def test_filter_pages_keeps_both_sides_when_neither_blank():
    pages = [PagePair(_content_image(), _content_image())]

    partitions = filter_pages(pages)

    assert partitions == [[pages[0].front, pages[0].back]]
