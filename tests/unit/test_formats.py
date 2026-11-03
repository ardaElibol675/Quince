import pytest

from quince.core import formats
from quince.core.formats import Category


def test_ids_and_extensions_are_unique_and_lowercase():
    ids = [f.id for f in formats.FORMATS]
    assert len(ids) == len(set(ids))
    exts = [e for f in formats.FORMATS for e in f.extensions]
    assert len(exts) == len(set(exts))
    assert all(e == e.lower() and not e.startswith(".") for e in exts)


def test_every_category_has_formats_and_every_format_has_a_mime_type():
    for category in Category:
        assert formats.in_category(category), category
        assert category.label and category.icon
    for fmt in formats.FORMATS:
        assert fmt.mime_type and fmt.extension and fmt.label and fmt.name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("photo.jpg", ("photo", "jpg")),
        ("photo.JPEG", ("photo", "JPEG")),
        ("archive.tar.gz", ("archive", "tar.gz")),
        ("archive.TAR.GZ", ("archive", "TAR.GZ")),
        ("my.report.v2.docx", ("my.report.v2", "docx")),
        (".bashrc", (".bashrc", "")),
        ("Makefile", ("Makefile", "")),
        ("trailing.", ("trailing.", "")),
        (".tar.gz", (".tar", "gz")),
    ],
)
def test_split_extension(name, expected):
    assert formats.split_extension(name) == expected


@pytest.mark.parametrize(
    ("name", "format_id"),
    [
        ("a.jpg", "jpg"),
        ("a.JPEG", "jpg"),
        ("a.tif", "tiff"),
        ("a.tar.gz", "targz"),
        ("a.tgz", "targz"),
        ("a.tar", "tar"),
        ("a.markdown", "md"),
        ("a.htm", "html"),
        ("a.xyz", None),
        ("noext", None),
    ],
)
def test_from_filename(name, format_id):
    fmt = formats.from_filename(name)
    assert (fmt.id if fmt else None) == format_id


def test_get_find_position():
    assert formats.get("png").category is Category.IMAGE
    assert formats.find("nope") is None
    with pytest.raises(KeyError):
        formats.get("nope")
    assert formats.position("jpg") < formats.position("png") < formats.position("mp3")


def test_text_formats():
    text = set(formats.text_format_ids())
    assert {"txt", "md", "csv", "tsv", "srt", "vtt", "html", "svg", "rtf"} <= text
    assert "png" not in text and "docx" not in text
