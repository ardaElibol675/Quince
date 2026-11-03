import zipfile

import pytest

from quince.core import filetype, formats

FTYP = b"\x00\x00\x00\x18ftyp"


@pytest.mark.parametrize(
    ("head", "best"),
    [
        (b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "png"),
        (b"\xff\xd8\xff\xe0\0\x10JFIF", "jpg"),
        (b"GIF89a\x01\0", "gif"),
        (b"RIFF\0\0\0\0WEBPVP8 ", "webp"),
        (b"RIFF\0\0\0\0WAVEfmt ", "wav"),
        (b"RIFF\0\0\0\0AVI LIST", "avi"),
        (b"FORM\0\0\0\0AIFFCOMM", "aiff"),
        (b"II*\0\x08\0\0\0", "tiff"),
        (b"MM\0*\0\0\0\x08", "tiff"),
        (b"BM6$\0\0\0\0\0\x006\0\0\0", "bmp"),
        (FTYP + b"heic\0\0\0\0mif1heic", "heic"),
        (FTYP + b"avif\0\0\0\0avifmif1", "avif"),
        (FTYP + b"mif1\0\0\0\0avifmiaf", "avif"),
        (FTYP + b"isom\0\0\x02\0isomiso2", "mp4"),
        (FTYP + b"M4A \0\0\x02\0M4A isom", "m4a"),
        (FTYP + b"qt  \0\0\x02\0qt  \0\0", "mov"),
        (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x84webm", "webm"),
        (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x88matroska", "mkv"),
        (b"OggS\0\x02" + b"\0" * 22 + b"\x01vorbis", "ogg"),
        (b"OggS\0\x02" + b"\0" * 22 + b"OpusHead", "opus"),
        (b"fLaC\0\0\0\x22", "flac"),
        (b"ID3\x04\0\0\0\0\0#", "mp3"),
        (b"\xff\xfb\x90\x64", "mp3"),
        (b"%PDF-1.7\n", "pdf"),
        (b"7z\xbc\xaf\x27\x1c\0\x04", "7z"),
        (b"Rar!\x1a\x07\x01\0", "rar"),
        (b"\x1f\x8b\x08\0", "targz"),
        (b"{\\rtf1\\ansi", "rtf"),
        (b"WEBVTT\n\n00:00.000 --> 00:01.000\nhi", "vtt"),
        (b"1\n00:00:00,000 --> 00:00:01,000\nhi\n", "srt"),
        (b"\xef\xbb\xbf1\r\n00:00:00,000 --> 00:00:01,000\r\n", "srt"),
        (b"<!DOCTYPE html>\n<html>", "html"),
        (b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg">', "svg"),
        (b"<svg width='1'/>", "svg"),
        (b"Hello world\n", "txt"),
        ("Merhaba dünya, şğıöç\n".encode(), "txt"),
        ("Merhaba dünya".encode("cp1254"), "txt"),
        ("hi".encode("utf-16"), "txt"),
    ],
)
def test_sniff_bytes_best_guess(head, best):
    assert filetype.sniff_bytes(head)[0] == best


def test_sniff_bytes_containers_list_every_sibling():
    assert set(filetype.sniff_bytes(FTYP + b"isom\0\0\0\0")) == {"mp4", "m4a", "mov"}
    assert set(filetype.sniff_bytes(b"OggS" + b"\0" * 30)) == {"ogg", "opus"}
    text = filetype.sniff_bytes(b"plain text")
    assert set(text) == set(formats.text_format_ids())


def test_sniff_bytes_tar_and_unknown():
    tar = bytearray(512)
    tar[257:262] = b"ustar"
    assert filetype.sniff_bytes(bytes(tar)) == ("tar",)
    assert filetype.sniff_bytes(b"") == ()
    assert filetype.sniff_bytes(b"\x00\x01\x02\x03binary\x00") == ()
    # "BM" alone is not enough for a bitmap.
    assert filetype.sniff_bytes(b"BMW cars are fast")[0] == "txt"
    # ADTS AAC has the frame sync but layer bits 00; it is not MP3.
    assert "mp3" not in filetype.sniff_bytes(b"\xff\xf1\x50\x80")


def test_resolve_rules():
    jpg, png, txt = formats.get("jpg"), formats.get("png"), formats.get("txt")
    assert filetype.resolve(jpg, ("jpg",)) is jpg
    assert filetype.resolve(jpg, ()) is jpg  # content tells nothing: extension wins
    assert filetype.resolve(jpg, ("png",)) is png  # content contradicts: content wins
    assert filetype.resolve(None, ("png",)) is png
    assert filetype.resolve(None, ()) is None
    assert filetype.resolve(formats.get("md"), filetype.sniff_bytes(b"# Title")).id == "md"
    assert filetype.resolve(None, filetype.sniff_bytes(b"# Title")) is txt


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_detect_zip_based_formats(tmp_path):
    docx = _zip(tmp_path / "doc.bin", {"[Content_Types].xml": "", "word/document.xml": ""})
    assert filetype.detect(docx).id == "docx"
    ods = _zip(tmp_path / "x", {"mimetype": "application/vnd.oasis.opendocument.spreadsheet"})
    assert filetype.detect(ods).id == "ods"
    epub = _zip(tmp_path / "book", {"mimetype": "application/epub+zip"})
    assert filetype.detect(epub).id == "epub"
    plain = _zip(tmp_path / "files", {"a.txt": "a"})
    assert filetype.detect(plain).id == "zip"
    # A real DOCX named .zip stays a ZIP archive, so archive tools apply.
    assert filetype.detect(docx.rename(tmp_path / "doc.zip")).id == "zip"


def test_detect_trusts_content_over_wrong_extension(tmp_path):
    fake = tmp_path / "photo.jpg"
    fake.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    assert filetype.detect(fake).id == "png"
    notes = tmp_path / "notes.csv"
    notes.write_text("a,b\n1,2\n")
    assert filetype.detect(notes).id == "csv"


def test_detect_unknown_empty_and_missing(tmp_path):
    blob = tmp_path / "blob"
    blob.write_bytes(b"\x00\x01\x02")
    assert filetype.detect(blob) is None
    empty = tmp_path / "empty.png"
    empty.touch()
    assert filetype.detect(empty).id == "png"
    assert filetype.detect(tmp_path / "missing.pdf").id == "pdf"
    assert filetype.detect(tmp_path / "missing") is None


def test_corrupt_zip_keeps_extension(tmp_path):
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"PK\x03\x04" + b"\0" * 40)
    assert filetype.detect(broken).id == "docx"
