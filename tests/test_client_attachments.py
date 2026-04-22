"""Tests for the attachment-related helpers in :mod:`client`."""

from __future__ import annotations

import os
import tempfile

from confluence_markdown_mcp.client import _encode_multipart, file_sha1


def test_encode_multipart_includes_fields_and_file():
    body, ctype = _encode_multipart(
        fields=[("minorEdit", "true"), ("comment", "hi")],
        filename="cat.png",
        content=b"PNG-BYTES",
        content_type="image/png",
    )
    assert ctype.startswith("multipart/form-data; boundary=")
    assert b'name="minorEdit"' in body
    assert b"true" in body
    assert b'name="comment"' in body
    assert b"hi" in body
    assert b'name="file"; filename="cat.png"' in body
    assert b"Content-Type: image/png" in body
    assert b"PNG-BYTES" in body
    # Body must terminate with the closing boundary.
    boundary = ctype.split("boundary=", 1)[1].encode()
    assert body.rstrip(b"\r\n").endswith(b"--" + boundary + b"--")


def test_encode_multipart_escapes_quotes_in_filename():
    body, _ = _encode_multipart(
        fields=[],
        filename='a"b.png',
        content=b"x",
        content_type="application/octet-stream",
    )
    # The quote must be backslash-escaped inside the Content-Disposition
    # header so a crafted filename cannot break out.
    assert b'filename="a\\"b.png"' in body


def test_file_sha1_hashes_content():
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(b"hello world")
        path = fh.name
    try:
        digest = file_sha1(path)
        # sha1("hello world")
        assert digest == "2aae6c35c94fcfb415dbe95f408b9ce91ee846ed"
    finally:
        os.unlink(path)
