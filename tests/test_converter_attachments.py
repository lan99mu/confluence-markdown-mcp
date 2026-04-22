"""Round-trip tests for images and attachment-backed links."""

from __future__ import annotations

from confluence_markdown_mcp.converter import (
    markdown_to_storage,
    storage_to_markdown,
)


def test_image_with_attachment_round_trip():
    storage = (
        '<p><ac:image ac:alt="foo" ac:width="200" ac:height="100">'
        '<ri:attachment ri:filename="hello.png" />'
        "</ac:image></p>"
    )
    md = storage_to_markdown(storage)
    assert "![foo](attachments/hello.png)" in md
    assert "<!--cm-image" in md
    assert 'width="200"' in md
    assert 'height="100"' in md

    back = markdown_to_storage(md)
    assert '<ri:attachment ri:filename="hello.png"' in back
    assert 'ac:alt="foo"' in back
    assert 'ac:width="200"' in back
    assert 'ac:height="100"' in back


def test_image_with_external_url_round_trip():
    storage = (
        '<p><ac:image><ri:url ri:value="https://example.com/p.png" /></ac:image></p>'
    )
    md = storage_to_markdown(storage)
    assert "![](https://example.com/p.png)" in md

    back = markdown_to_storage(md)
    assert '<ri:url ri:value="https://example.com/p.png"' in back
    assert "<ri:attachment" not in back


def test_attachment_link_round_trip():
    storage = (
        "<p><ac:link>"
        '<ri:attachment ri:filename="doc.pdf" />'
        "<ac:plain-text-link-body><![CDATA[Download the doc]]></ac:plain-text-link-body>"
        "</ac:link></p>"
    )
    md = storage_to_markdown(storage)
    assert "[Download the doc](attachments/doc.pdf)<!--cm-attachment-->" in md

    back = markdown_to_storage(md)
    assert '<ri:attachment ri:filename="doc.pdf"' in back
    assert "<![CDATA[Download the doc]]>" in back


def test_orphan_image_comment_is_dropped():
    # If the user removes an image but leaves the marker behind, we must
    # not emit an unsafe HTML comment back to Confluence.
    md = "Plain text <!--cm-image width=\"10\"-->"
    back = markdown_to_storage(md)
    assert "<!--cm-image" not in back
    assert "cm-attachment" not in back


def test_attachment_filename_with_unsafe_chars():
    storage = (
        '<p><ac:image><ri:attachment ri:filename="a/../b.png" /></ac:image></p>'
    )
    md = storage_to_markdown(storage)
    # Path components must never appear in the Markdown target.
    assert "attachments/b.png" in md
    assert ".." not in md


def test_image_src_with_attachments_subdir_strips_directory():
    # When re-serialising, only the basename is kept – Confluence does
    # not support directory-scoped filenames.
    md = "![cat](attachments/cat.png)"
    back = markdown_to_storage(md)
    assert 'ri:filename="cat.png"' in back
    assert "attachments/" not in back
