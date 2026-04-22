"""Storage XHTML → Markdown using an ``lxml`` element tree + visitor.

This replaces the previous ``HTMLParser``/regex implementation.  Working on
a real tree lets us handle nested structures (lists inside tables, lists
inside blockquotes, styled spans inside links, etc.) without the fragile
counter-based state machine the old parser needed.

Macros (``<ac:structured-macro>`` and ``<ac:task-list>``) are still
pre-processed into inert placeholder tokens or plain ``<ul>`` markup via
:mod:`confluence_markdown_mcp.converter.macros` so this module never has
to special-case Confluence custom elements.
"""

from __future__ import annotations

import re
from typing import List, Optional

from lxml import etree, html as lxml_html

from ._iframe import render_iframe
from ._style import build_span_style, extract_align
from .macros import postprocess_markdown, preprocess_storage


_INLINE_TAGS = {
    "a",
    "strong",
    "b",
    "em",
    "i",
    "code",
    "span",
    "u",
    "s",
    "strike",
    "del",
    "ins",
    "sub",
    "sup",
    "font",
    "br",
    "img",
}


def storage_to_markdown(storage_html: str) -> str:
    """Convert Confluence storage-format XHTML to Markdown."""

    if not storage_html:
        return ""

    processed, replacements = preprocess_storage(storage_html)

    # Wrap in a root so lxml always returns a single element, and feed
    # through its forgiving HTML parser so tags like ``ac:foo`` (should
    # any still be present) don't trigger namespace errors.
    wrapped = f"<div id=\"__cm_root__\">{processed}</div>"
    parser = etree.HTMLParser(recover=True)
    tree = etree.fromstring(wrapped, parser)
    root = None
    if tree is not None:
        # ``fromstring`` with an HTML parser wraps the input in
        # ``<html><body>…</body></html>``.  Find our sentinel div.
        for el in tree.iter():
            if el.tag == "div" and el.get("id") == "__cm_root__":
                root = el
                break
    if root is None:
        return ""

    renderer = _Renderer()
    renderer.visit_children(root)
    markdown_text = renderer.finish()
    markdown_text = postprocess_markdown(markdown_text, replacements)

    # Drop list markers that carry no content (template scaffolding).
    markdown_text = re.sub(
        r"(?m)^[ \t]*(?:[-*]|\d+\.)[ \t]*$\n?", "", markdown_text
    )
    markdown_text = re.sub(r"[ \t]+\n", "\n", markdown_text)
    markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text)
    return markdown_text.strip() + "\n"


class _Renderer:
    """Recursive visitor that accumulates Markdown fragments."""

    def __init__(self) -> None:
        self._out: List[str] = []
        # Stack of writable buffers – the top of the stack is where
        # ``_emit`` appends.  A new buffer is pushed while rendering the
        # contents of e.g. a ``<td>`` so the cell text can be post-
        # processed before being stitched into the output.
        self._stack: List[List[str]] = [self._out]
        self._pre_depth = 0
        self._list_stack: List[str] = []   # "ul" / "ol"
        self._ol_counters: List[int] = []
        # Blockquote state – number of currently-open ``<blockquote>`` levels.
        # A post-processing pass prefixes the appropriate number of ``> ``.
        self._in_blockquote = 0

    # --------------------------------------------------------------- core
    def finish(self) -> str:
        return "".join(self._out)

    def _emit(self, text: str) -> None:
        if text:
            self._stack[-1].append(text)

    def _push_buffer(self) -> List[str]:
        buf: List[str] = []
        self._stack.append(buf)
        return buf

    def _pop_buffer(self) -> str:
        buf = self._stack.pop()
        return "".join(buf)

    # -------------------------------------------------------------- visit
    def visit_children(self, element) -> None:
        # lxml interleaves element.text (before first child) and each
        # child element plus its ``.tail`` (text after that child,
        # still belonging to the parent).
        if element.text:
            self._visit_text(element.text)
        for child in element:
            self.visit(child)
            if child.tail:
                self._visit_text(child.tail)

    def _visit_text(self, text: str) -> None:
        if self._pre_depth:
            self._emit(text.replace("\r", ""))
            return
        cleaned = re.sub(r"[ \t]+", " ", text.replace("\r", ""))
        self._emit(cleaned)

    def visit(self, element) -> None:
        tag = element.tag
        if not isinstance(tag, str):
            # Comments, processing instructions, etc. – skip.
            return

        # Drop script / style wholesale – text content is ignored.
        if tag in ("script", "style"):
            return

        method = getattr(self, f"_tag_{tag.replace('-', '_').replace(':', '_')}", None)
        if method is not None:
            method(element)
            return

        # Unknown / structural tag: descend but emit nothing for the tag
        # itself.  Matches the legacy parser's behaviour.
        self.visit_children(element)

    # ---------------------------------------------------------- block tags
    def _heading(self, element, level: int) -> None:
        align = extract_align(element.get("style", ""))
        self._emit("\n\n" + "#" * level + " ")
        if align:
            self._emit(f'<span style="text-align: {align}">')
        self.visit_children(element)
        if align:
            self._emit("</span>")
        self._emit("\n\n")

    def _tag_h1(self, el): self._heading(el, 1)
    def _tag_h2(self, el): self._heading(el, 2)
    def _tag_h3(self, el): self._heading(el, 3)
    def _tag_h4(self, el): self._heading(el, 4)
    def _tag_h5(self, el): self._heading(el, 5)
    def _tag_h6(self, el): self._heading(el, 6)

    def _tag_p(self, el) -> None:
        align = extract_align(el.get("style", ""))
        if align:
            self._emit(f'\n\n<p style="text-align: {align}">')
        else:
            self._emit("\n\n")
        self.visit_children(el)
        if align:
            self._emit("</p>\n\n")
        else:
            self._emit("\n\n")

    def _tag_hr(self, el) -> None:
        self._emit("\n\n---\n\n")

    def _tag_br(self, el) -> None:
        self._emit("  \n")

    def _tag_pre(self, el) -> None:
        self._pre_depth += 1
        self._emit("\n\n```\n")
        self.visit_children(el)
        self._emit("\n```\n\n")
        self._pre_depth -= 1

    def _tag_blockquote(self, el) -> None:
        inner_buf = self._push_buffer()
        self._in_blockquote += 1
        self.visit_children(el)
        self._in_blockquote -= 1
        inner = self._pop_buffer()
        quoted = _prefix_blockquote(inner)
        self._emit("\n\n" + quoted + "\n\n")

    # ---------------------------------------------------------- inline tags
    def _wrap(self, el, opener: str, closer: str) -> None:
        self._emit(opener)
        self.visit_children(el)
        self._emit(closer)

    def _tag_strong(self, el): self._wrap(el, "**", "**")
    def _tag_b(self, el):      self._wrap(el, "**", "**")
    def _tag_em(self, el):     self._wrap(el, "*", "*")
    def _tag_i(self, el):      self._wrap(el, "*", "*")
    def _tag_u(self, el):      self._wrap(el, "*", "*")

    def _tag_code(self, el) -> None:
        if self._pre_depth:
            self.visit_children(el)
            return
        self._wrap(el, "`", "`")

    def _tag_a(self, el) -> None:
        href = el.get("href", "")
        self._emit("[")
        self.visit_children(el)
        self._emit(f"]({href})")

    def _tag_img(self, el) -> None:
        alt = el.get("alt", "")
        src = el.get("src", "")
        self._emit(f"![{alt}]({src})")

    def _styled_span(self, el) -> None:
        style = build_span_style(el.get("style", ""))
        if style:
            self._emit(f'<span style="{style}">')
            self.visit_children(el)
            self._emit("</span>")
        else:
            self.visit_children(el)

    def _tag_span(self, el): self._styled_span(el)

    def _tag_font(self, el) -> None:
        # Convert legacy ``<font color="…">`` to a styled span.
        color = (el.get("color") or "").strip()
        from ._style import SAFE_COLOR_RE  # local import to avoid cycle cost
        pieces = []
        if color and SAFE_COLOR_RE.match(color):
            pieces.append(f"color: {color}")
        style = build_span_style(el.get("style", ""))
        if style:
            pieces.append(style)
        combined = "; ".join(pieces)
        if combined:
            self._emit(f'<span style="{combined}">')
            self.visit_children(el)
            self._emit("</span>")
        else:
            self.visit_children(el)

    # Inline tags that the old parser did not have dedicated handling for
    # are treated as transparent – descend into their content.  (The old
    # behaviour was essentially the same.)

    # --------------------------------------------------------------- lists
    def _list_indent(self) -> str:
        return "  " * max(len(self._list_stack) - 1, 0)

    def _tag_ul(self, el) -> None:
        self._list_stack.append("ul")
        self.visit_children(el)
        self._list_stack.pop()
        if not self._list_stack:
            self._emit("\n\n")

    def _tag_ol(self, el) -> None:
        self._list_stack.append("ol")
        self._ol_counters.append(0)
        self.visit_children(el)
        self._list_stack.pop()
        self._ol_counters.pop()
        if not self._list_stack:
            self._emit("\n\n")

    def _tag_li(self, el) -> None:
        indent = self._list_indent()
        if self._list_stack and self._list_stack[-1] == "ol":
            self._ol_counters[-1] += 1
            marker = f"{self._ol_counters[-1]}."
        else:
            marker = "-"
        self._emit(f"\n{indent}{marker} ")
        self.visit_children(el)

    # -------------------------------------------------------------- tables
    def _tag_table(self, el) -> None:
        rows: List[List[str]] = []
        has_header = False
        for row_el in el.iter("tr"):
            row: List[str] = []
            row_is_header = False
            for cell in row_el:
                tag = cell.tag
                if not isinstance(tag, str) or tag not in ("td", "th"):
                    continue
                self._push_buffer()
                self.visit_children(cell)
                cell_text = self._pop_buffer()
                row.append(_normalise_cell(cell_text))
                if tag == "th":
                    row_is_header = True
            if row:
                rows.append(row)
                if row_is_header:
                    has_header = True

        rows = [row for row in rows if row]
        if not rows:
            return
        max_cols = max(len(r) for r in rows)
        rows = [r + [""] * (max_cols - len(r)) for r in rows]

        if has_header:
            header = rows[0]
            body = rows[1:]
        else:
            header = [f"col{i + 1}" for i in range(max_cols)]
            body = rows

        self._emit("\n\n")
        self._emit("| " + " | ".join(header) + " |\n")
        self._emit("| " + " | ".join(["---"] * max_cols) + " |\n")
        for row in body:
            self._emit("| " + " | ".join(row) + " |\n")
        self._emit("\n")

    def _tag_thead(self, el): self.visit_children(el)
    def _tag_tbody(self, el): self.visit_children(el)
    def _tag_tr(self, el):    self.visit_children(el)
    def _tag_td(self, el):    self.visit_children(el)
    def _tag_th(self, el):    self.visit_children(el)

    # -------------------------------------------------------------- iframe
    def _tag_iframe(self, el) -> None:
        attrs = {k: v for k, v in el.attrib.items()}
        rendered = render_iframe(attrs)
        if rendered:
            self._emit(f"\n\n{rendered}\n\n")


def _prefix_blockquote(text: str) -> str:
    """Prefix non-empty lines of ``text`` with ``"> "``.

    Leading and trailing blank lines are stripped first so we don't end up
    with ``>`` lines wrapping the entire block; intermediate blank lines
    become bare ``>`` to keep them part of the same blockquote.
    """

    stripped = text.strip("\n")
    if not stripped:
        return ""
    lines = []
    for line in stripped.splitlines():
        if line.strip():
            lines.append(f"> {line}")
        else:
            lines.append(">")
    return "\n".join(lines)


def _normalise_cell(text: str) -> str:
    """Flatten a rendered cell so it fits on a single Markdown-table row."""

    text = text.replace("\r", "").replace("|", "\\|")
    lines: List[str] = []
    for raw in text.split("\n"):
        match = re.match(r"[ \t]*", raw)
        leading = match.group(0) if match else ""
        rest = raw[len(leading):]
        rest = re.sub(r"[ \t]+", " ", rest).rstrip()
        if not rest:
            continue
        if re.fullmatch(r"(?:[-*]|\d+\.)", rest):
            continue
        indent_width = len(leading.expandtabs(2))
        lines.append(("&nbsp;" * indent_width) + rest)
    return "<br>".join(lines)
