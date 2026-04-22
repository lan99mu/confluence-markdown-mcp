"""Convert Confluence storage XHTML into Markdown."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import List, Optional

from .macros import postprocess_markdown, preprocess_storage


class _StorageParser(HTMLParser):
    """Stream parser that builds Markdown from Confluence storage XHTML.

    The parser handles the common HTML subset used by Confluence (headings,
    paragraphs, inline emphasis, links, lists, tables, code/pre, line
    breaks and images).  Macros are expected to have been stripped out of
    the input via :func:`macros.preprocess_storage` – they become inert
    placeholder tokens that pass through :meth:`handle_data` unchanged.
    """

    # Inline tags that must never introduce line breaks on their own.
    _INLINE_TAGS = {"a", "strong", "b", "em", "i", "code", "span", "u"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: List[str] = []
        self._link_stack: List[str] = []
        self._list_stack: List[str] = []   # "ul" or "ol"
        self._ol_counters: List[int] = []
        self._pre_depth = 0
        self._skip_depth = 0  # ignore children of dropped elements
        # Stack of closing strings to emit when a styled wrapper (e.g. a
        # coloured <span>) ends.  ``""`` means the tag carried no style and
        # should be a no-op on close.
        self._style_stack: List[str] = []

        # Table state.
        self._in_table = False
        self._table_rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None
        self._row_is_header = False
        self._has_header_row = False

    # ------------------------------------------------------------------ core
    def result(self) -> str:
        text = "".join(self._out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        # Drop list markers that carry no content (``-``, ``1.`` alone on a
        # line, optionally indented).  They come from empty Confluence
        # placeholders such as ``<ul><li></li><li></li></ul>`` in template
        # scaffolding.
        text = re.sub(r"(?m)^[ \t]*(?:[-*]|\d+\.)[ \t]*$\n?", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n"

    # --------------------------------------------------------------- helpers
    def _emit(self, text: str) -> None:
        if self._cell is not None:
            self._cell.append(text)
        else:
            self._out.append(text)

    def _list_indent(self) -> str:
        return "  " * max(len(self._list_stack) - 1, 0)

    # --------------------------------------------------------------- tags
    def handle_starttag(self, tag, attrs):  # noqa: D401 - HTMLParser API
        if self._skip_depth:
            self._skip_depth += 1
            return

        attrs_dict = dict(attrs)

        if tag in ("script", "style"):
            self._skip_depth = 1
            return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self._emit("\n\n" + "#" * level + " ")
            return

        if tag == "p":
            self._emit("\n\n")
            return

        if tag == "br":
            self._emit("  \n")
            return

        if tag == "hr":
            self._emit("\n\n---\n\n")
            return

        if tag in ("strong", "b"):
            self._emit("**")
            return
        if tag in ("em", "i"):
            self._emit("*")
            return
        if tag == "u":
            # Markdown has no native underline; fall back to emphasis.
            self._emit("*")
            return
        if tag == "code" and self._pre_depth == 0:
            self._emit("`")
            return
        if tag == "pre":
            self._pre_depth += 1
            self._emit("\n\n```\n")
            return

        if tag == "a":
            self._emit("[")
            self._link_stack.append(attrs_dict.get("href", ""))
            return

        if tag == "span":
            color = _extract_color(attrs_dict.get("style", ""))
            if color:
                self._emit(f'<span style="color: {color}">')
                self._style_stack.append("</span>")
            else:
                self._style_stack.append("")
            return

        if tag == "font":
            color = attrs_dict.get("color", "").strip()
            if not color:
                color = _extract_color(attrs_dict.get("style", ""))
            if color:
                self._emit(f'<span style="color: {color}">')
                self._style_stack.append("</span>")
            else:
                self._style_stack.append("")
            return

        if tag == "img":
            alt = attrs_dict.get("alt", "")
            src = attrs_dict.get("src", "")
            self._emit(f"![{alt}]({src})")
            return

        if tag == "ul":
            self._list_stack.append("ul")
            return
        if tag == "ol":
            self._list_stack.append("ol")
            self._ol_counters.append(0)
            return
        if tag == "li":
            indent = self._list_indent()
            if self._list_stack and self._list_stack[-1] == "ol":
                self._ol_counters[-1] += 1
                marker = f"{self._ol_counters[-1]}."
            else:
                marker = "-"
            self._emit(f"\n{indent}{marker} ")
            return

        if tag == "table":
            self._in_table = True
            self._table_rows = []
            self._has_header_row = False
            return
        if tag == "thead":
            return
        if tag == "tbody":
            return
        if tag == "tr":
            self._row = []
            self._row_is_header = False
            return
        if tag in ("td", "th"):
            self._cell = []
            if tag == "th":
                self._row_is_header = True
            return

        if tag == "blockquote":
            # Open a blockquote segment – we append "> " at line starts via
            # a simple scheme: push a marker that handle_data uses.  Since
            # Markdown blockquote is line-based, we instead just emit a
            # newline and let callers prefix; keeping things simple, wrap
            # the emitted children in blockquote via post-processing is
            # overkill – HTMLParser streams linearly, so we just emit a
            # `> ` prefix after each newline until the closing tag.
            self._emit("\n\n> ")
            return

        # Silently ignore unknown/structural tags – their text content is
        # preserved through handle_data.

    def handle_endtag(self, tag):
        if self._skip_depth:
            self._skip_depth -= 1
            return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._emit("\n\n")
            return
        if tag == "p":
            self._emit("\n\n")
            return
        if tag in ("strong", "b"):
            self._emit("**")
            return
        if tag in ("em", "i", "u"):
            self._emit("*")
            return
        if tag == "code" and self._pre_depth == 0:
            self._emit("`")
            return
        if tag == "pre":
            self._pre_depth = max(self._pre_depth - 1, 0)
            self._emit("\n```\n\n")
            return
        if tag == "a":
            href = self._link_stack.pop() if self._link_stack else ""
            self._emit(f"]({href})")
            return
        if tag in ("span", "font"):
            if self._style_stack:
                closer = self._style_stack.pop()
                if closer:
                    self._emit(closer)
            return
        if tag in ("ul", "ol"):
            if self._list_stack and self._list_stack[-1] == tag:
                self._list_stack.pop()
                if tag == "ol" and self._ol_counters:
                    self._ol_counters.pop()
            if not self._list_stack:
                self._emit("\n\n")
            return
        if tag == "li":
            return

        if tag in ("td", "th"):
            if self._cell is not None and self._row is not None:
                cell_text = _normalise_cell("".join(self._cell))
                self._row.append(cell_text)
                self._cell = None
            return
        if tag == "tr":
            if self._row is not None:
                self._table_rows.append(self._row)
                if self._row_is_header:
                    self._has_header_row = True
                self._row = None
            return
        if tag == "table":
            self._flush_table()
            self._in_table = False
            return

        if tag == "blockquote":
            self._emit("\n\n")
            return

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._pre_depth:
            self._emit(data)
            return
        # Collapse runs of whitespace but keep significant newlines outside
        # of <pre>.
        cleaned = re.sub(r"[ \t]+", " ", data.replace("\r", ""))
        self._emit(cleaned)

    # --------------------------------------------------------------- tables
    def _flush_table(self) -> None:
        rows = [row for row in self._table_rows if row]
        if not rows:
            return

        max_cols = max(len(r) for r in rows)
        rows = [r + [""] * (max_cols - len(r)) for r in rows]

        if self._has_header_row:
            header = rows[0]
            body = rows[1:]
        else:
            header = [f"col{i + 1}" for i in range(max_cols)]
            body = rows

        self._out.append("\n\n")
        self._out.append("| " + " | ".join(header) + " |\n")
        self._out.append("| " + " | ".join(["---"] * max_cols) + " |\n")
        for row in body:
            self._out.append("| " + " | ".join(row) + " |\n")
        self._out.append("\n")


def _normalise_cell(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # Pipes and newlines must not leak into a Markdown table row.
    return text.replace("|", "\\|")


_COLOR_RE = re.compile(r"(?i)color\s*:\s*([^;]+?)\s*(?:;|$)")


def _extract_color(style: str) -> str:
    """Return the CSS ``color`` value from a ``style`` attribute, if any."""

    if not style:
        return ""
    match = _COLOR_RE.search(style)
    if not match:
        return ""
    color = match.group(1).strip()
    # Basic sanitisation: reject values that look like they try to break out
    # of the style attribute.
    if any(c in color for c in "\"<>"):
        return ""
    return color


def storage_to_markdown(storage_html: str) -> str:
    """Convert a Confluence storage-format XHTML string to Markdown."""

    if not storage_html:
        return ""

    processed, replacements = preprocess_storage(storage_html)
    parser = _StorageParser()
    parser.feed(processed)
    parser.close()
    markdown_text = parser.result()
    markdown_text = postprocess_markdown(markdown_text, replacements)
    # Final clean up – collapse stray blank lines again because macro
    # insertions may have produced more than two newlines in a row.
    markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text)
    return markdown_text.strip() + "\n"
