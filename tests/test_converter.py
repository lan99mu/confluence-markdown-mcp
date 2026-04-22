"""Basic round-trip tests for the Markdown ↔ storage converter."""

from __future__ import annotations

from confluence_markdown_mcp.converter import (
    markdown_to_storage,
    storage_to_markdown,
)


def test_headings_paragraph_inline():
    storage = (
        "<h1>Title</h1>"
        "<p>Hello <strong>world</strong> and <em>friends</em>.</p>"
    )
    md = storage_to_markdown(storage)
    assert "# Title" in md
    assert "**world**" in md
    assert "*friends*" in md


def test_code_macro_round_trip():
    storage = (
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">python</ac:parameter>'
        '<ac:plain-text-body><![CDATA[print("hi")]]></ac:plain-text-body>'
        "</ac:structured-macro>"
    )
    md = storage_to_markdown(storage)
    assert "```python" in md
    assert 'print("hi")' in md

    back = markdown_to_storage(md)
    assert 'ac:name="code"' in back
    assert 'ac:name="language">python' in back
    assert "<![CDATA[print(\"hi\")]]>" in back


def test_admonition_round_trip():
    storage = (
        '<ac:structured-macro ac:name="info">'
        "<ac:rich-text-body><p>Be careful.</p></ac:rich-text-body>"
        "</ac:structured-macro>"
    )
    md = storage_to_markdown(storage)
    assert "> [!INFO]" in md
    assert "Be careful." in md

    back = markdown_to_storage(md)
    assert 'ac:name="info"' in back
    assert "Be careful." in back


def test_lists_and_table():
    storage = (
        "<ul><li>one</li><li>two</li></ul>"
        "<table><tbody>"
        "<tr><th>A</th><th>B</th></tr>"
        "<tr><td>1</td><td>2</td></tr>"
        "</tbody></table>"
    )
    md = storage_to_markdown(storage)
    assert "- one" in md
    assert "- two" in md
    assert "| A | B |" in md
    assert "| 1 | 2 |" in md

    back = markdown_to_storage(md)
    assert "<ul>" in back and "<li>one</li>" in back
    assert "<table>" in back and "<th>A</th>" in back


def test_link_round_trip():
    storage = '<p>See <a href="https://example.com">example</a></p>'
    md = storage_to_markdown(storage)
    assert "[example](https://example.com)" in md
    back = markdown_to_storage(md)
    assert '<a href="https://example.com">example</a>' in back


def test_unknown_macro_round_trip():
    storage = (
        '<p>Before.</p>'
        '<ac:structured-macro ac:name="jira">'
        '<ac:parameter ac:name="key">DOC-1</ac:parameter>'
        "</ac:structured-macro>"
        "<p>After.</p>"
    )
    md = storage_to_markdown(storage)
    assert "Before." in md and "After." in md
    back = markdown_to_storage(md)
    assert 'ac:name="jira"' in back
    assert 'DOC-1' in back


def test_inline_code_not_mangled_by_bold():
    md = "A `*x*` literal and **bold** word.\n"
    storage = markdown_to_storage(md)
    assert "<code>*x*</code>" in storage
    assert "<strong>bold</strong>" in storage
