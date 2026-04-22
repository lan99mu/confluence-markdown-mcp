"""Idempotency / round-trip fuzz tests for the AST-based converter.

The new engine rewrites both directions as AST visitors rather than
regex pipelines.  These tests guard the critical property that
``storage → md → storage → md`` reaches a fixed point – i.e. the
Markdown produced the first time is stable through a second conversion
cycle.  Without this guarantee every pull + push on an unchanged page
would subtly drift the content and accumulate diff noise.
"""

from __future__ import annotations

import pytest

from confluence_markdown_mcp.converter import (
    markdown_to_storage,
    storage_to_markdown,
)


SAMPLES = [
    # Headings + inline formatting + link.
    (
        "<h1>Title</h1>"
        "<p>Hello <strong>world</strong> and <em>friends</em>.</p>"
        '<p>See <a href="https://example.com">example</a></p>'
    ),
    # Code macro with language.
    (
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">python</ac:parameter>'
        '<ac:plain-text-body><![CDATA[print("hi")\nx = 1]]></ac:plain-text-body>'
        "</ac:structured-macro>"
    ),
    # Admonition blockquote.
    (
        '<ac:structured-macro ac:name="info">'
        "<ac:rich-text-body><p>Be careful.</p></ac:rich-text-body>"
        "</ac:structured-macro>"
    ),
    # Nested unordered lists.
    (
        "<ul>"
        "<li>one<ul><li>one-a</li><li>one-b</li></ul></li>"
        "<li>two</li>"
        "</ul>"
    ),
    # Ordered list.
    "<ol><li>first</li><li>second</li><li>third</li></ol>",
    # Simple two-column table.
    (
        "<table><tbody>"
        "<tr><th>A</th><th>B</th></tr>"
        "<tr><td>1</td><td>2</td></tr>"
        "<tr><td>3</td><td>4</td></tr>"
        "</tbody></table>"
    ),
    # Single-column table (exercises the ``| --- |`` separator path).
    (
        "<table><tbody>"
        "<tr><th>col</th></tr>"
        '<tr><td>v1 <span style="color: red">r</span></td></tr>'
        "</tbody></table>"
    ),
    # Color / background-color spans.
    (
        "<p>Hello "
        '<span style="color: rgb(255,0,0)">red</span>'
        " and "
        '<span style="background-color: #ffff00">hl</span>'
        "!</p>"
    ),
    # Paragraph alignment.
    '<p style="text-align: center">Centered text</p>',
    # Task list – confluence native.
    (
        "<ac:task-list>"
        "<ac:task><ac:task-id>1</ac:task-id>"
        "<ac:task-status>incomplete</ac:task-status>"
        "<ac:task-body>alpha</ac:task-body></ac:task>"
        "<ac:task><ac:task-id>2</ac:task-id>"
        "<ac:task-status>complete</ac:task-status>"
        "<ac:task-body>beta</ac:task-body></ac:task>"
        "</ac:task-list>"
    ),
    # Unknown macro – must round-trip as an opaque block.
    (
        '<p>Before.</p>'
        '<ac:structured-macro ac:name="jira">'
        '<ac:parameter ac:name="key">DOC-1</ac:parameter>'
        "</ac:structured-macro>"
        "<p>After.</p>"
    ),
    # Iframe wrapped in html-bobswift.
    (
        '<ac:structured-macro ac:name="html-bobswift">'
        '<ac:plain-text-body><![CDATA[<iframe '
        'src="https://viewer.diagrams.net/?edit=_blank#Gabc" '
        'width="800" height="600" frameborder="0" allowfullscreen></iframe>]]>'
        '</ac:plain-text-body>'
        "</ac:structured-macro>"
    ),
    # Mixed inline HTML pass-through tags.
    "<p>A <u>underlined</u> and <s>struck</s> and <sup>up</sup> bit.</p>",
]


@pytest.mark.parametrize("storage", SAMPLES)
def test_storage_md_round_trip_is_idempotent(storage: str) -> None:
    """``storage → md → storage → md`` must reach a stable fixed point.

    We compare the Markdown output of the first and second pulls.  Any
    drift here would translate into spurious diffs on every
    pull/push cycle on an unchanged page.
    """

    md1 = storage_to_markdown(storage)
    storage2 = markdown_to_storage(md1)
    md2 = storage_to_markdown(storage2)
    assert md1 == md2, (
        "Markdown diverged on second conversion:\n"
        f"--- first pull ---\n{md1}\n--- second pull ---\n{md2}"
    )
