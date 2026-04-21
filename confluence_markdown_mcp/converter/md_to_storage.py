"""Convert Markdown back to Confluence *storage* XHTML.

The conversion is intentionally conservative: it covers the constructs that
``storage_to_markdown`` can produce so that a ``pull → edit → push`` round
trip preserves the structure of a page.  Anything unrecognised is emitted
as a plain paragraph – that matches Confluence's own behaviour when it
receives unknown markup.

Supported constructs
--------------------

* ATX headings ``# … ######``
* Paragraphs (blank-line separated)
* Fenced code blocks (``` ``` ```` with optional language) → ``code`` macro
* Unordered (``-``/``*``) and ordered (``1.``) lists, with 2-space
  nested indentation
* GFM blockquotes including ``> [!INFO]`` admonition headers → info / note /
  warning / tip macros
* Simple pipe-delimited tables with a ``---`` header separator
* Inline: ``**bold**``, ``*italic*``, ``` `code` ``` and ``[label](url)``
* HTML-comment round-trip tokens for unknown macros (see ``macros.py``)
"""

from __future__ import annotations

import html
import re
from typing import List, Optional, Tuple

from .macros import ADMONITIONS, UNKNOWN_MACRO_RE

_ADMONITION_HEADER_RE = re.compile(
    r"^\[!(?P<name>INFO|NOTE|WARNING|TIP)\]\s*$",
    re.IGNORECASE,
)

_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.*)$")
_UL_RE = re.compile(r"^(?P<indent>\s*)[-*]\s+(?P<text>.*)$")
_OL_RE = re.compile(r"^(?P<indent>\s*)\d+\.\s+(?P<text>.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def markdown_to_storage(markdown_text: str) -> str:
    """Convert a Markdown document to Confluence storage-format XHTML."""

    # First splice any unknown-macro comment tokens back out of the Markdown
    # so they are not literally rendered; they become direct storage XML.
    unknown_chunks: List[str] = []

    def _pull_unknown(match: "re.Match[str]") -> str:
        body = html.unescape(match.group("body"))
        name = html.unescape(match.group("name"))
        unknown_chunks.append(
            f'<ac:structured-macro ac:name="{html.escape(name, quote=True)}">'
            f"{body}</ac:structured-macro>"
        )
        return f"\0UNKN{len(unknown_chunks) - 1}\0"

    normalised = UNKNOWN_MACRO_RE.sub(_pull_unknown, markdown_text)

    lines = normalised.splitlines()
    renderer = _BlockRenderer(lines)
    rendered = renderer.render()

    # Re-insert unknown macros.
    def _sub_unknown(match: "re.Match[str]") -> str:
        idx = int(match.group(1))
        return unknown_chunks[idx] if 0 <= idx < len(unknown_chunks) else ""

    return re.sub(r"\0UNKN(\d+)\0", _sub_unknown, rendered)


# --------------------------------------------------------------------------
# Block level rendering
# --------------------------------------------------------------------------


class _BlockRenderer:
    def __init__(self, lines: List[str]) -> None:
        self.lines = lines
        self.i = 0
        self.out: List[str] = []

    # ------------------------------------------------------------------ api
    def render(self) -> str:
        while self.i < len(self.lines):
            line = self.lines[self.i]

            if not line.strip():
                self.i += 1
                continue

            if line.startswith("```"):
                self._render_code_fence()
                continue

            heading = _HEADING_RE.match(line)
            if heading:
                level = len(heading.group("hashes"))
                text = _render_inline(heading.group("text").strip())
                self.out.append(f"<h{level}>{text}</h{level}>")
                self.i += 1
                continue

            if line.lstrip().startswith(">"):
                self._render_blockquote()
                continue

            if _UL_RE.match(line) or _OL_RE.match(line):
                self._render_list()
                continue

            if self._looks_like_table(self.i):
                self._render_table()
                continue

            # Fallback: paragraph gathered from consecutive non-blank lines.
            self._render_paragraph()

        return "".join(self.out)

    # -------------------------------------------------------------- blocks
    def _render_code_fence(self) -> None:
        fence = self.lines[self.i]
        language = fence[3:].strip()
        self.i += 1
        buf: List[str] = []
        while self.i < len(self.lines) and not self.lines[self.i].startswith("```"):
            buf.append(self.lines[self.i])
            self.i += 1
        # Consume closing fence if present.
        if self.i < len(self.lines):
            self.i += 1

        safe_code = "\n".join(buf).replace("]]>", "]]]]><![CDATA[>")
        lang_xml = (
            f'<ac:parameter ac:name="language">{html.escape(language, quote=True)}'
            f"</ac:parameter>"
            if language
            else ""
        )
        self.out.append(
            '<ac:structured-macro ac:name="code">'
            f"{lang_xml}"
            f"<ac:plain-text-body><![CDATA[{safe_code}]]></ac:plain-text-body>"
            "</ac:structured-macro>"
        )

    def _render_blockquote(self) -> None:
        # Collect contiguous blockquote lines (starting with '>').
        content_lines: List[str] = []
        while self.i < len(self.lines) and self.lines[self.i].lstrip().startswith(">"):
            stripped = self.lines[self.i].lstrip()[1:]
            # A single leading space after '>' is the canonical form.
            if stripped.startswith(" "):
                stripped = stripped[1:]
            content_lines.append(stripped)
            self.i += 1

        admonition: Optional[str] = None
        if content_lines:
            header_match = _ADMONITION_HEADER_RE.match(content_lines[0].strip())
            if header_match:
                admonition = header_match.group("name").lower()
                content_lines = content_lines[1:]

        inner_md = "\n".join(content_lines).strip("\n")
        inner_html = markdown_to_storage(inner_md) if inner_md else ""

        if admonition and admonition in ADMONITIONS:
            self.out.append(
                f'<ac:structured-macro ac:name="{admonition}">'
                f"<ac:rich-text-body>{inner_html}</ac:rich-text-body>"
                "</ac:structured-macro>"
            )
        else:
            self.out.append(f"<blockquote>{inner_html}</blockquote>")

    def _render_list(self) -> None:
        rendered, _ = _collect_list(self.lines, self.i, indent_level=0)
        self.out.append(rendered)
        # Advance ``i`` past the list block – ``_collect_list`` returned the
        # number of consumed lines via self.i mutation below.
        self.i = _collect_list_end_index[0]

    def _render_table(self) -> None:
        start = self.i
        # Header row.
        header_cells = _split_pipe_row(self.lines[self.i])
        self.i += 2  # skip header + separator
        body_rows: List[List[str]] = []
        while self.i < len(self.lines) and "|" in self.lines[self.i]:
            body_rows.append(_split_pipe_row(self.lines[self.i]))
            self.i += 1

        header_html = "".join(
            f"<th>{_render_inline(c)}</th>" for c in header_cells
        )
        body_html = "".join(
            "<tr>" + "".join(f"<td>{_render_inline(c)}</td>" for c in row) + "</tr>"
            for row in body_rows
        )
        self.out.append(
            f"<table><thead><tr>{header_html}</tr></thead>"
            f"<tbody>{body_html}</tbody></table>"
        )
        _ = start  # kept for readability

    def _render_paragraph(self) -> None:
        buf: List[str] = []
        while (
            self.i < len(self.lines)
            and self.lines[self.i].strip()
            and not self.lines[self.i].startswith("```")
            and not self.lines[self.i].lstrip().startswith(">")
            and not _HEADING_RE.match(self.lines[self.i])
            and not _UL_RE.match(self.lines[self.i])
            and not _OL_RE.match(self.lines[self.i])
            and not self._looks_like_table(self.i)
        ):
            buf.append(self.lines[self.i])
            self.i += 1
        text = " ".join(s.strip() for s in buf).strip()
        if text:
            self.out.append(f"<p>{_render_inline(text)}</p>")

    # -------------------------------------------------------------- tables
    def _looks_like_table(self, idx: int) -> bool:
        if idx + 1 >= len(self.lines):
            return False
        header = self.lines[idx]
        sep = self.lines[idx + 1]
        return "|" in header and bool(_TABLE_SEP_RE.match(sep))


# ------------------------------------------------------------ list helpers
# A bit of a hack: ``_collect_list`` needs to communicate back to the
# renderer how many lines it consumed.  Using a mutable sentinel keeps the
# public signature cleaner.
_collect_list_end_index = [0]


def _collect_list(
    lines: List[str],
    start: int,
    indent_level: int,
) -> Tuple[str, int]:
    """Render a (possibly nested) list starting at ``lines[start]``.

    Returns ``(html, next_line_index)``.
    """

    items: List[str] = []
    ordered_marker = _OL_RE.match(lines[start])
    tag = "ol" if ordered_marker else "ul"
    i = start

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            # Blank line may still be part of the list when followed by
            # another item with the same indent; peek ahead.
            j = i + 1
            if j < len(lines) and (_UL_RE.match(lines[j]) or _OL_RE.match(lines[j])):
                i = j
                continue
            break

        ul = _UL_RE.match(line)
        ol = _OL_RE.match(line)
        match = ul or ol
        if not match:
            break

        indent = len(match.group("indent").expandtabs(4))
        level = indent // 2

        if level < indent_level:
            break

        if level > indent_level:
            # Nested list – recurse; attach to the previous item.
            nested, new_i = _collect_list(lines, i, indent_level + 1)
            if items:
                items[-1] = items[-1][: -len("</li>")] + nested + "</li>"
            i = new_i
            continue

        text = _render_inline(match.group("text").strip())
        items.append(f"<li>{text}</li>")
        i += 1

    _collect_list_end_index[0] = i
    return f"<{tag}>" + "".join(items) + f"</{tag}>", i


def _split_pipe_row(line: str) -> List[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    # Split on unescaped pipes.
    parts: List[str] = []
    buf: List[str] = []
    i = 0
    while i < len(stripped):
        ch = stripped[i]
        if ch == "\\" and i + 1 < len(stripped) and stripped[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "|":
            parts.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf).strip())
    return parts


# --------------------------------------------------------------------------
# Inline rendering
# --------------------------------------------------------------------------


_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_RE = re.compile(r"\*(.+?)\*", re.DOTALL)
_LINK_RE = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<url>[^)\s]+)\)")
_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)\)")


def _render_inline(text: str) -> str:
    """Convert a single line's worth of inline Markdown to HTML.

    Inline code spans are extracted *first* so that their contents are not
    touched by the bold / italic substitutions that follow.
    """

    # Also pass through unknown-macro placeholders untouched – they are
    # pure text as far as Markdown is concerned.
    code_spans: List[str] = []

    def _stash_code(match: "re.Match[str]") -> str:
        code_spans.append(match.group(1))
        return f"\0CODE{len(code_spans) - 1}\0"

    text = _INLINE_CODE_RE.sub(_stash_code, text)

    # Stash images and links before HTML-escaping so their URLs survive.
    links: List[str] = []

    def _stash_image(match: "re.Match[str]") -> str:
        alt = html.escape(match.group("alt"), quote=True)
        src = html.escape(match.group("src"), quote=True)
        links.append(f'<ac:image><ri:url ri:value="{src}" ri:title="{alt}" /></ac:image>')
        return f"\0LINK{len(links) - 1}\0"

    def _stash_link(match: "re.Match[str]") -> str:
        label = match.group("label")
        url = html.escape(match.group("url"), quote=True)
        links.append(f'<a href="{url}">{html.escape(label)}</a>')
        return f"\0LINK{len(links) - 1}\0"

    text = _IMAGE_RE.sub(_stash_image, text)
    text = _LINK_RE.sub(_stash_link, text)

    escaped = html.escape(text)
    escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITALIC_RE.sub(r"<em>\1</em>", escaped)

    # Put the stashed pieces back.
    def _restore_code(match: "re.Match[str]") -> str:
        idx = int(match.group(1))
        return f"<code>{html.escape(code_spans[idx])}</code>"

    def _restore_link(match: "re.Match[str]") -> str:
        idx = int(match.group(1))
        return links[idx]

    escaped = re.sub(r"\0CODE(\d+)\0", _restore_code, escaped)
    escaped = re.sub(r"\0LINK(\d+)\0", _restore_link, escaped)
    return escaped
