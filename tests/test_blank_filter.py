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


def test_filter_pages_splits_on_fully_blank_separator_sheet():
    doc1_page = PagePair(_content_image(), _content_image())
    separator = PagePair(_blank_image(), _blank_image())
    doc2_page = PagePair(_content_image(), _content_image())
    pages = [doc1_page, separator, doc2_page]

    partitions = filter_pages(pages, split_on_blank=True)

    assert len(partitions) == 2
    assert partitions[0] == [doc1_page.front, doc1_page.back]
    assert partitions[1] == [doc2_page.front, doc2_page.back]


def test_filter_pages_without_split_on_blank_ignores_separator_boundary():
    doc1_page = PagePair(_content_image(), _content_image())
    separator = PagePair(_blank_image(), _blank_image())
    doc2_page = PagePair(_content_image(), _content_image())
    pages = [doc1_page, separator, doc2_page]

    partitions = filter_pages(pages, split_on_blank=False)

    assert len(partitions) == 1
    assert partitions[0] == [
        doc1_page.front,
        doc1_page.back,
        doc2_page.front,
        doc2_page.back,
    ]
