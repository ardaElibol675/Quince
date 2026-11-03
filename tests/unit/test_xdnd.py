from quince.input.xdnd import parse_uri_list


def test_parse_uri_list():
    data = (
        b"# comment\r\n"
        b"file:///tmp/a%20b.png\r\n"
        b"file://localhost/home/arda/T%C3%BCrk%C3%A7e.pdf\r\n"
        b"https://example.com/x.png\r\n"
        b"file://otherhost/share/y.png\r\n"
        b"\r\n"
        b"file:///tmp/last"
    )
    assert parse_uri_list(data) == ("/tmp/a b.png", "/home/arda/Türkçe.pdf", "/tmp/last")


def test_parse_uri_list_empty_and_garbage():
    assert parse_uri_list(b"") == ()
    assert parse_uri_list(b"just some text\nmore") == ()
    assert parse_uri_list(b"\xff\xfe") == ()
