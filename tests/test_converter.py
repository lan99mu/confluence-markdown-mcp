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


def test_color_span_round_trip():
    storage = (
        '<p>Hello <span style="color: rgb(255,0,0)">red</span> '
        'and <span style="color: #00ff00">green</span>!</p>'
    )
    md = storage_to_markdown(storage)
    assert '<span style="color: rgb(255,0,0)">red</span>' in md
    assert '<span style="color: #00ff00">green</span>' in md

    back = markdown_to_storage(md)
    assert '<span style="color: rgb(255,0,0)">red</span>' in back
    assert '<span style="color: #00ff00">green</span>' in back


def test_color_span_rejects_css_injection():
    """Unsafe CSS colour payloads must not leak into the output."""

    cases = [
        ("expression(alert(1))", "expression"),
        ("red{}/*", "{"),
        ("url(x)", "url"),
        ("rgb(1,2,3); xss", "xss"),
    ]
    for payload, forbidden in cases:
        storage = f'<p><span style="color: {payload}">x</span></p>'
        md = storage_to_markdown(storage)
        assert forbidden not in md
        assert markdown_to_storage(md).find(forbidden) == -1


def test_task_list_inside_table_cell_preserves_checkboxes_and_breaks():
    """Task-list items inside a ``<td>`` must keep their line breaks and
    checkbox markers.  Confluence emits whitespace between ``<ac:task>``
    elements which previously caused the cell to collapse into one line
    such as ``- [ ] A - [ ] B`` – losing both the list breaks and any
    nested indentation.
    """

    storage = (
        "<table><tbody><tr><td>"
        "<ac:task-list>"
        "  <ac:task><ac:task-id>1</ac:task-id>"
        "<ac:task-status>incomplete</ac:task-status>"
        "<ac:task-body>有，架构设计地址是：</ac:task-body></ac:task>"
        "  <ac:task><ac:task-id>2</ac:task-id>"
        "<ac:task-status>incomplete</ac:task-status>"
        "<ac:task-body>无，原因是：</ac:task-body></ac:task>"
        "</ac:task-list>"
        "</td></tr></tbody></table>"
    )
    md = storage_to_markdown(storage)
    # Find the table row with the cell content.
    cell_row = next(
        line for line in md.splitlines()
        if "有，架构设计地址是" in line
    )
    # Both checkboxes survive.
    assert cell_row.count("- [ ]") == 2
    # They are separated by ``<br>`` rather than collapsed onto one line.
    assert "- [ ] 有，架构设计地址是：<br>- [ ] 无，原因是：" in cell_row
    # Leaked ``<ac:*>`` text must not appear.
    assert "incomplete" not in md


def test_nested_list_in_table_cell_preserves_indentation():
    storage = (
        "<table><tbody><tr><td>"
        "<ul><li>parent<ul><li>child</li></ul></li></ul>"
        "</td></tr></tbody></table>"
    )
    md = storage_to_markdown(storage)
    cell_row = next(line for line in md.splitlines() if "parent" in line)
    # Child item must be offset with non-breaking spaces so the hierarchy
    # remains visible inside a single-line Markdown table cell.
    assert "- parent<br>&nbsp;&nbsp;- child" in cell_row


def test_task_list_converted_to_task_markers():
    storage = (
        "<p>任务：</p>"
        "<ac:task-list>"
        "<ac:task><ac:task-id>1</ac:task-id>"
        "<ac:task-status>incomplete</ac:task-status>"
        "<ac:task-body>写代码</ac:task-body></ac:task>"
        "<ac:task><ac:task-id>2</ac:task-id>"
        "<ac:task-status>complete</ac:task-status>"
        "<ac:task-body>写测试</ac:task-body></ac:task>"
        "</ac:task-list>"
    )
    md = storage_to_markdown(storage)
    assert "- [ ] 写代码" in md
    assert "- [x] 写测试" in md
    # No leaking of ``<ac:task-id>`` / ``<ac:task-status>`` text.
    assert "incomplete" not in md
    assert "complete" not in md


def test_empty_placeholder_scaffolding_is_stripped():
    storage = (
        "<p>Before.</p>"
        "<p></p><ul><li></li><li></li></ul><p></p>"
        "<p>After.</p>"
    )
    md = storage_to_markdown(storage)
    assert "Before." in md and "After." in md
    # No stray empty list markers on their own line.
    for line in md.splitlines():
        assert line.strip() not in {"-", "*"}


# --------------------------------------------------------------------- push
# Regression tests for the three reported issues:
#   1. HTML-style markup written in Markdown must be preserved on push.
#   2. Colour / background-colour spans must round-trip.
#   3. Block alignment must survive pull + push; task-list checkboxes must
#      round-trip back into a Confluence ``<ac:task-list>`` on push.


def test_push_preserves_inline_html_styles():
    md = "A <u>underlined</u> and <s>struck</s> and <sup>up</sup> bit.\n"
    storage = markdown_to_storage(md)
    assert "<u>underlined</u>" in storage
    assert "<s>struck</s>" in storage
    assert "<sup>up</sup>" in storage
    # The surrounding text must still be emitted as normal paragraph HTML
    # rather than escaped.
    assert "&lt;u&gt;" not in storage


def test_push_preserves_linebreak_tag():
    md = "line one<br>line two\n"
    storage = markdown_to_storage(md)
    assert "<br/>" in storage
    assert "&lt;br" not in storage


def test_push_preserves_markdown_soft_linebreak():
    md = "line one\nline two\n"
    storage = markdown_to_storage(md)
    assert "<p>line one<br/>line two</p>" in storage


def test_json_fence_downgrades_to_javascript_code_macro():
    md = '```json\n{"ok": true}\n```\n'
    storage = markdown_to_storage(md)
    assert 'ac:name="code"' in storage
    assert '<ac:parameter ac:name="language">javascript</ac:parameter>' in storage
    assert "<![CDATA[{\"ok\": true}]]>" in storage
    assert ">json</ac:parameter>" not in storage


def test_push_preserves_background_color_span():
    md = '<span style="background-color: #ffff00">highlight</span>\n'
    storage = markdown_to_storage(md)
    assert 'background-color: #ffff00' in storage
    assert '<span style="background-color: #ffff00">highlight</span>' in storage


def test_push_preserves_combined_color_and_background():
    md = '<span style="color: red; background-color: yellow">mix</span>\n'
    storage = markdown_to_storage(md)
    assert 'color: red' in storage
    assert 'background-color: yellow' in storage


def test_color_span_inside_list_round_trip():
    storage = (
        "<ul>"
        '<li>a <span style="color: red">red</span> item</li>'
        "<li>plain</li>"
        "</ul>"
    )
    md = storage_to_markdown(storage)
    assert '<span style="color: red">red</span>' in md
    back = markdown_to_storage(md)
    assert '<span style="color: red">red</span>' in back
    assert "<ul>" in back and "<li>" in back


def test_pull_preserves_paragraph_alignment():
    storage = (
        '<p style="text-align: center">Centered text</p>'
        '<p style="text-align: right">Right-aligned</p>'
        "<p>plain</p>"
    )
    md = storage_to_markdown(storage)
    assert '<p style="text-align: center">Centered text</p>' in md
    assert '<p style="text-align: right">Right-aligned</p>' in md


def test_paragraph_alignment_round_trip():
    storage = '<p style="text-align: center">Hello</p>'
    md = storage_to_markdown(storage)
    back = markdown_to_storage(md)
    assert '<p style="text-align: center">Hello</p>' in back


def test_paragraph_alignment_rejects_unsafe_values():
    storage = '<p style="text-align: url(evil)">oops</p>'
    md = storage_to_markdown(storage)
    assert "url(evil)" not in md
    # Falls back to a plain paragraph.
    assert "oops" in md


def test_task_list_push_round_trip():
    md = "- [ ] todo one\n- [x] done two\n"
    storage = markdown_to_storage(md)
    assert "<ac:task-list>" in storage
    assert "<ac:task-status>incomplete</ac:task-status>" in storage
    assert "<ac:task-status>complete</ac:task-status>" in storage
    assert "todo one" in storage and "done two" in storage
    # The plain-bullet form must not leak through when the whole list is a
    # task list.
    assert "<ul>" not in storage
    # Full storage → md → storage round trip keeps the macro.
    md2 = storage_to_markdown(storage)
    assert "- [ ] todo one" in md2
    assert "- [x] done two" in md2


def test_mixed_list_is_not_converted_to_task_list():
    md = "- [ ] a task\n- regular bullet\n"
    storage = markdown_to_storage(md)
    # One of the items is not a task marker, so we keep the plain list
    # rather than silently dropping its content into a task macro.
    assert "<ac:task-list>" not in storage
    assert "<ul>" in storage


def test_push_rejects_unsafe_style_declarations():
    # Only ``color`` and ``background-color`` declarations with allow-listed
    # values survive; arbitrary CSS properties must be dropped so crafted
    # markdown cannot smuggle styles into the storage XML.
    md = (
        '<span style="color: red; font-size: 99px; '
        'background: url(javascript:alert(1))">x</span>\n'
    )
    storage = markdown_to_storage(md)
    assert "color: red" in storage
    assert "font-size" not in storage
    assert "javascript" not in storage
    assert "url(" not in storage


# --- drawio / iframe embeds via html-bobswift macro ---------------------


def test_pull_html_bobswift_macro_exposes_iframe():
    """``html-bobswift`` wraps raw HTML in a CDATA block.  On pull the
    body must be spliced back out so a contained ``<iframe>`` becomes a
    Markdown iframe line.
    """

    storage = (
        '<p>Before.</p>'
        '<ac:structured-macro ac:name="html-bobswift">'
        '<ac:plain-text-body><![CDATA[<iframe '
        'src="https://viewer.diagrams.net/?edit=_blank#G123" '
        'width="800" height="600" frameborder="0"></iframe>]]>'
        '</ac:plain-text-body>'
        "</ac:structured-macro>"
        "<p>After.</p>"
    )
    md = storage_to_markdown(storage)
    assert "Before." in md and "After." in md
    assert '<iframe' in md
    assert 'src="https://viewer.diagrams.net/?edit=_blank#G123"' in md
    assert 'width="800"' in md
    assert 'height="600"' in md


def test_push_iframe_wraps_in_html_bobswift():
    md = (
        'Hello.\n\n'
        '<iframe src="https://viewer.diagrams.net/#G123" '
        'width="800" height="600" frameborder="0"></iframe>\n\n'
        'World.\n'
    )
    storage = markdown_to_storage(md)
    assert 'ac:name="html-bobswift"' in storage
    assert '<ac:plain-text-body><![CDATA[<iframe' in storage
    assert 'src="https://viewer.diagrams.net/#G123"' in storage
    assert "<p>Hello.</p>" in storage
    assert "<p>World.</p>" in storage


def test_iframe_full_round_trip():
    storage = (
        '<ac:structured-macro ac:name="html-bobswift">'
        '<ac:plain-text-body><![CDATA[<iframe '
        'src="https://viewer.diagrams.net/?edit=_blank#Gabc" '
        'width="800" height="600" frameborder="0" allowfullscreen></iframe>]]>'
        '</ac:plain-text-body>'
        "</ac:structured-macro>"
    )
    md = storage_to_markdown(storage)
    back = markdown_to_storage(md)
    assert 'ac:name="html-bobswift"' in back
    assert 'src="https://viewer.diagrams.net/?edit=_blank#Gabc"' in back
    assert 'width="800"' in back
    assert 'height="600"' in back
    assert 'frameborder="0"' in back
    assert 'allowfullscreen' in back


def test_iframe_rejects_unsafe_src_on_pull():
    storage = '<p>hi</p><iframe src="javascript:alert(1)"></iframe>'
    md = storage_to_markdown(storage)
    # Iframe is dropped entirely – never leaks ``javascript:``.
    assert "javascript" not in md
    assert "<iframe" not in md
    assert "hi" in md


def test_iframe_rejects_unsafe_src_on_push():
    md = '<iframe src="javascript:alert(1)"></iframe>\n'
    storage = markdown_to_storage(md)
    assert "javascript" not in storage
    # No html-bobswift wrapper should be emitted for a dropped iframe.
    assert "html-bobswift" not in storage


def test_iframe_strips_non_allowlisted_attributes():
    """Attributes outside the allow-list (e.g. ``onload``) must not
    survive the push conversion as live attributes.
    """

    md = (
        '<iframe src="https://example.com/d" onload="alert(1)" '
        'sandbox="allow-scripts" width="200"></iframe>\n'
    )
    storage = markdown_to_storage(md)
    # The iframe must be wrapped in html-bobswift (proving the line was
    # recognised as an iframe block) …
    assert "html-bobswift" in storage
    assert 'src="https://example.com/d"' in storage
    assert 'width="200"' in storage
    # … and the non-allow-listed attributes must be dropped.
    assert "onload" not in storage
    assert "sandbox" not in storage
    assert "alert" not in storage


def test_bare_iframe_pull_round_trip():
    """An ``<iframe>`` directly in the storage XHTML (no wrapping
    macro) must also survive a pull + push round trip.  On push we
    always wrap in ``html-bobswift`` so Confluence renders the embed.
    """

    storage = (
        '<p>Before.</p>'
        '<iframe src="https://example.com/diagram" width="400" '
        'height="300"></iframe>'
        "<p>After.</p>"
    )
    md = storage_to_markdown(storage)
    assert '<iframe src="https://example.com/diagram"' in md
    back = markdown_to_storage(md)
    assert 'ac:name="html-bobswift"' in back
    assert 'src="https://example.com/diagram"' in back


def test_push_iframe_preserves_layout_style():
    """Layout CSS on ``<iframe>`` (border/margin/max-width/display etc.)
    must survive the push sanitiser — previously only colour/align was
    kept and everything else was silently dropped.
    """

    md = (
        '<iframe src="https://example.com/d" '
        'style="border: none; max-width: 100%; margin: 0 auto; display: block">'
        '</iframe>\n'
    )
    storage = markdown_to_storage(md)
    assert "html-bobswift" in storage
    assert "border: none" in storage
    assert "max-width: 100%" in storage
    assert "margin: 0 auto" in storage
    assert "display: block" in storage


def test_push_iframe_preserves_dimension_style():
    md = (
        '<iframe src="https://example.com/d" '
        'style="width: 800px; height: 600px"></iframe>\n'
    )
    storage = markdown_to_storage(md)
    assert "width: 800px" in storage
    assert "height: 600px" in storage


def test_push_iframe_style_drops_unsafe_declarations():
    """Unsafe CSS payloads (``expression(...)``, ``url(javascript:...)``)
    must never survive, while neighbouring safe declarations are kept.
    """

    md = (
        '<iframe src="https://example.com/d" '
        'style="expression(alert(1)); color: red; '
        'background: url(javascript:alert(1))"></iframe>\n'
    )
    storage = markdown_to_storage(md)
    assert "expression" not in storage
    assert "javascript" not in storage
    assert "url(" not in storage
    assert "color: red" in storage


def test_push_iframe_without_blank_lines_still_wrapped():
    """When the user writes an ``<iframe>`` without surrounding blank
    lines markdown-it folds it into a larger html_block alongside the
    neighbouring text.  We must still wrap the iframe in an
    ``html-bobswift`` macro – emitting a raw ``<iframe>`` into
    Confluence storage is rejected by the server.
    """

    md = (
        '<iframe src="https://example.com/pic.png" width="800"></iframe>\n'
        'more text\n'
    )
    storage = markdown_to_storage(md)
    assert 'ac:name="html-bobswift"' in storage
    assert '<ac:plain-text-body><![CDATA[<iframe' in storage
    assert 'src="https://example.com/pic.png"' in storage
    # The raw iframe tag must never leak through unwrapped.
    assert '<iframe' not in storage.replace(
        '<![CDATA[<iframe', ''
    )


def test_push_multiple_iframes_in_one_block_each_wrapped():
    md = (
        '<iframe src="https://a.example.com/x.png"></iframe>\n'
        '<iframe src="https://b.example.com/y.png"></iframe>\n'
    )
    storage = markdown_to_storage(md)
    assert storage.count('ac:name="html-bobswift"') == 2
    assert 'src="https://a.example.com/x.png"' in storage
    assert 'src="https://b.example.com/y.png"' in storage


def test_push_iframe_adjacent_text_unsafe_src_dropped():
    """An unsafe-src iframe embedded in a larger html_block must be
    dropped while the surrounding text is preserved.
    """

    md = (
        '<iframe src="javascript:alert(1)"></iframe>\n'
        'keep me\n'
    )
    storage = markdown_to_storage(md)
    assert 'javascript' not in storage
    assert 'html-bobswift' not in storage
    assert 'keep me' in storage


def test_single_column_table_round_trip():
    """Single-column tables emitted by ``storage_to_markdown`` (``| --- |``
    separator) must round-trip back to a ``<table>`` – otherwise the cell
    contents, including any inline ``<span>`` / ``<br>`` wrappers, leak
    into a plain paragraph on push.
    """

    storage = (
        "<table><tbody>"
        "<tr><th>是否有架构设计</th></tr>"
        '<tr><td>有<br/><span style="color: red">不一致</span></td></tr>'
        "</tbody></table>"
    )
    md = storage_to_markdown(storage)
    # The emitted separator has a single ``---`` group.
    assert "| --- |" in md

    back = markdown_to_storage(md)
    assert "<table>" in back
    assert "<th>是否有架构设计</th>" in back
    assert "<td>" in back
    assert '<span style="color: red">不一致</span>' in back
    assert "<br/>" in back
    # The literal pipes from the Markdown separator must not leak into
    # the storage XML as paragraph text.
    assert "| --- |" not in back


def test_single_column_table_does_not_eat_horizontal_rule_like_lines():
    """A bare ``---`` line (no surrounding pipes) must still be treated as
    a normal paragraph, not a single-column table separator.
    """

    md = "Some text\n\n---\n\nMore text\n"
    storage = markdown_to_storage(md)
    assert "<table>" not in storage
