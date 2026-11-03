"""Helpers for XDND data (pure logic, no X11)."""

from __future__ import annotations

from urllib.parse import unquote, urlsplit


def parse_uri_list(data: bytes) -> tuple[str, ...]:
    """Local file paths from a ``text/uri-list`` payload (RFC 2483).

    Comment lines and non-file URIs (http, smb, ...) are skipped; ``file://host/...`` is
    accepted only for an empty host or ``localhost``.
    """
    paths = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = urlsplit(line)
        if parts.scheme != "file" or parts.netloc not in ("", "localhost"):
            continue
        path = unquote(parts.path)
        if path.startswith("/"):
            paths.append(path)
    return tuple(paths)
