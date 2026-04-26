"""Markdown → Confluence storage-format XHTML via ``markdown-it-py`` tokens.

The previous implementation hand-rolled a line scanner plus a stack of
regex substitutions to convert Markdown into the storage format.  That
approach produced the correct output for the common happy path but had
well-known failure modes around inline tokenisation (e.g. ``*`` / `` ` ``
edge cases), HTML allow-listing, nested lists in tables, and alignment
wrappers.

This module replaces the scanner with a real Markdown parser
(``markdown-it-py``, the reference CommonMark parser in Python) and
walks the produced token stream to emit storage XML.  The public API
(:func:`markdown_to_storage`) is unchanged so the rest of the package
needs no modification.
"""

from __future__ import annotations

import html
import os
import re
from typing import Dict, List, Optional, Tuple

from markdown_it import MarkdownIt
from markdown_it.token import Token

from ._iframe import parse_iframe_markup, render_iframe
from ._style import SAFE_ALIGN_VALUES, build_span_style, extract_align
from .macros import (
    ADMONITIONS,
    ATTACHMENTS_DIRNAME,
    ATTACHMENT_LINK_MARKER_RE,
    IMAGE_ATTR_COMMENT_RE,
    UNKNOWN_MACRO_RE,
    parse_image_attr_comment,
    sanitize_attachment_filename,
)


# ---------------------------------------------------------------------------
# Markdown parser
# ---------------------------------------------------------------------------


def _build_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"html": True, "breaks": False})
    parser.enable(["table", "strikethrough"])
    return parser


_MD = _build_parser()


_UNKNOWN_MACRO_PLACEHOLDER_OPEN = "\ue010UNKN"
_UNKNOWN_MACRO_PLACEHOLDER_CLOSE = "\ue011"
_UNKNOWN_MACRO_PLACEHOLDER_RE = re.compile(r"\ue010UNKN(\d+)\ue011")


def markdown_to_storage(markdown_text: str) -> str:
    """Convert a Markdown document to Confluence storage-format XHTML."""

    # Pull unknown-macro comment tokens out before parsing – they become
    # direct storage XML and must not be touched by the Markdown parser.
    unknown_chunks: List[str] = []

    def _pull(match: "re.Match[str]") -> str:
        body = html.unescape(match.group("body"))
        name = html.unescape(match.group("name"))
        unknown_chunks.append(
            f'<ac:structured-macro ac:name="{html.escape(name, quote=True)}">'
            f"{body}</ac:structured-macro>"
        )
        idx = len(unknown_chunks) - 1
        return f"{_UNKNOWN_MACRO_PLACEHOLDER_OPEN}{idx}{_UNKNOWN_MACRO_PLACEHOLDER_CLOSE}"

    normalised = UNKNOWN_MACRO_RE.sub(_pull, markdown_text)

    tokens = _MD.parse(normalised)
    renderer = _BlockRenderer(tokens)
    rendered = renderer.render()

    def _restore_unknown(match: "re.Match[str]") -> str:
        idx = int(match.group(1))
        return unknown_chunks[idx] if 0 <= idx < len(unknown_chunks) else ""

    return _UNKNOWN_MACRO_PLACEHOLDER_RE.sub(_restore_unknown, rendered)


# ---------------------------------------------------------------------------
# Block-level token walker
# ---------------------------------------------------------------------------


_ADMONITION_HEADER_RE = re.compile(
    r"^\[!(?P<name>INFO|NOTE|WARNING|TIP)\]\s*$",
    re.IGNORECASE,
)
_TASK_MARKER_RE = re.compile(r"^\[(?P<mark>[ xX])\]\s+(?P<body>.*)$", re.DOTALL)
_ALIGN_P_RE = re.compile(
    r'^\s*<p\s+style="text-align:\s*(?P<align>left|right|center|justify)\s*"\s*>'
    r"(?P<body>.*)</p>\s*$",
    re.DOTALL | re.IGNORECASE,
)
_IFRAME_BLOCK_RE = re.compile(
    r"^\s*<iframe\b[^>]*>\s*</iframe>\s*$|^\s*<iframe\b[^>]*/\s*>\s*$",
    re.IGNORECASE | re.DOTALL,
)
# Non-anchored scanner: finds individual ``<iframe ...></iframe>`` (or
# self-closing ``<iframe .../>``) tags inside a larger html_block so
# we can still wrap them in an ``html-bobswift`` macro even when the
# user didn't leave blank lines around the iframe.  Confluence storage
# format does not accept raw ``<iframe>``.
_IFRAME_SCAN_RE = re.compile(
    r"<iframe\b[^>]*?(?:/\s*>|>\s*</iframe\s*>)",
    re.IGNORECASE | re.DOTALL,
)
_SPAN_OPEN_RE = re.compile(
    r'^<span\s+style="(?P<style>[^"<>]*)"\s*>$',
    re.IGNORECASE,
)
_INLINE_HTML_PASSTHROUGH = re.compile(
    r"^</?(?:u|s|strike|del|ins|sub|sup)\s*/?>$",
    re.IGNORECASE,
)
_BR_RE = re.compile(r"^<br\s*/?\s*>$", re.IGNORECASE)
_CODE_LANGUAGE_FALLBACKS = {
    "json": "javascript",
}


class _BlockRenderer:
    def __init__(self, tokens: List[Token]) -> None:
        self.tokens = tokens
        self.i = 0
        self.out: List[str] = []

    # --------------------------------------------------------------- api
    def render(self) -> str:
        while self.i < len(self.tokens):
            tok = self.tokens[self.i]
            handler = getattr(self, f"_do_{tok.type}", None)
            if handler is None:
                # Unknown token type – skip it.
                self.i += 1
                continue
            handler(tok)
        return "".join(self.out)

    # ---------------------------------------------------------- utilities
    def _consume_until(self, close_type: str) -> List[Token]:
        """Collect tokens until a matching closer; returns [start..close-1]."""

        depth = 1
        start = self.i
        while self.i < len(self.tokens):
            tok = self.tokens[self.i]
            if tok.type == close_type.replace("_close", "_open"):
                depth += 1
            elif tok.type == close_type:
                depth -= 1
                if depth == 0:
                    break
            self.i += 1
        return self.tokens[start:self.i]

    # ------------------------------------------------------------ blocks
    def _do_heading_open(self, tok: Token) -> None:
        level = int(tok.tag[1])
        self.i += 1
        inline = self.tokens[self.i]
        self.i += 1  # inline
        self.i += 1  # heading_close
        text = _render_inline(inline.children or [])
        self.out.append(f"<h{level}>{text}</h{level}>")

    def _do_paragraph_open(self, tok: Token) -> None:
        self.i += 1
        inline = self.tokens[self.i]
        self.i += 1
        self.i += 1  # paragraph_close
        text = _render_inline(inline.children or [])
        if not text:
            return
        self.out.append(f"<p>{text}</p>")

    def _do_fence(self, tok: Token) -> None:
        self.i += 1
        language = _normalise_code_language((tok.info or "").strip())
        code = tok.content
        if code.endswith("\n"):
            code = code[:-1]
        safe = code.replace("]]>", "]]]]><![CDATA[>")
        lang_xml = (
            f'<ac:parameter ac:name="language">{html.escape(language, quote=True)}'
            f"</ac:parameter>"
            if language
            else ""
        )
        self.out.append(
            '<ac:structured-macro ac:name="code">'
            f"{lang_xml}"
            f"<ac:plain-text-body><![CDATA[{safe}]]></ac:plain-text-body>"
            "</ac:structured-macro>"
        )

    def _do_code_block(self, tok: Token) -> None:
        # Indented code block – treat the same as a fenced block with no lang.
        self.i += 1
        code = tok.content.rstrip("\n")
        safe = code.replace("]]>", "]]]]><![CDATA[>")
        self.out.append(
            '<ac:structured-macro ac:name="code">'
            f"<ac:plain-text-body><![CDATA[{safe}]]></ac:plain-text-body>"
            "</ac:structured-macro>"
        )

    def _do_hr(self, tok: Token) -> None:
        self.i += 1
        self.out.append("<hr/>")

    def _do_blockquote_open(self, tok: Token) -> None:
        self.i += 1  # skip blockquote_open
        # Collect children tokens until blockquote_close at matching depth.
        depth = 1
        inner: List[Token] = []
        while self.i < len(self.tokens):
            t = self.tokens[self.i]
            if t.type == "blockquote_open":
                depth += 1
            elif t.type == "blockquote_close":
                depth -= 1
                if depth == 0:
                    self.i += 1
                    break
            inner.append(t)
            self.i += 1

        # Detect admonition: first paragraph's inline starts with [!NAME].
        admonition = _detect_admonition(inner)
        sub = _BlockRenderer(inner)
        inner_xml = sub.render()

        if admonition in ADMONITIONS:
            self.out.append(
                f'<ac:structured-macro ac:name="{admonition}">'
                f"<ac:rich-text-body>{inner_xml}</ac:rich-text-body>"
                "</ac:structured-macro>"
            )
        else:
            self.out.append(f"<blockquote>{inner_xml}</blockquote>")

    # ------------------------------------------------------------- lists
    def _do_bullet_list_open(self, tok: Token) -> None:
        self.i += 1
        items: List[List[Token]] = []
        raw_texts: List[str] = []
        while self.i < len(self.tokens):
            t = self.tokens[self.i]
            if t.type == "bullet_list_close":
                self.i += 1
                break
            if t.type == "list_item_open":
                self.i += 1
                body: List[Token] = []
                depth = 1
                while self.i < len(self.tokens):
                    tt = self.tokens[self.i]
                    if tt.type == "list_item_open":
                        depth += 1
                    elif tt.type == "list_item_close":
                        depth -= 1
                        if depth == 0:
                            self.i += 1
                            break
                    body.append(tt)
                    self.i += 1
                items.append(body)
                raw_texts.append(_list_item_raw_text(body))
            else:
                self.i += 1

        # Task-list detection: all items are ``[ ]``/``[x]`` + space + body.
        if items and all(_TASK_MARKER_RE.match(t) for t in raw_texts):
            parts = ["<ac:task-list>"]
            for idx, body in enumerate(items, start=1):
                match = _TASK_MARKER_RE.match(raw_texts[idx - 1])
                assert match
                status = "complete" if match.group("mark").lower() == "x" else "incomplete"
                # Render the list-item body XML then strip the leading
                # task-marker text that the raw inline parser emitted.
                body_xml = _render_list_item(body)
                body_xml = _strip_leading_task_marker(body_xml)
                parts.append(
                    "<ac:task>"
                    f"<ac:task-id>{idx}</ac:task-id>"
                    f"<ac:task-status>{status}</ac:task-status>"
                    f"<ac:task-body>{body_xml}</ac:task-body>"
                    "</ac:task>"
                )
            parts.append("</ac:task-list>")
            self.out.append("".join(parts))
            return

        parts = ["<ul>"]
        for body in items:
            parts.append("<li>" + _render_list_item(body) + "</li>")
        parts.append("</ul>")
        self.out.append("".join(parts))

    def _do_ordered_list_open(self, tok: Token) -> None:
        self.i += 1
        items: List[List[Token]] = []
        while self.i < len(self.tokens):
            t = self.tokens[self.i]
            if t.type == "ordered_list_close":
                self.i += 1
                break
            if t.type == "list_item_open":
                self.i += 1
                body: List[Token] = []
                depth = 1
                while self.i < len(self.tokens):
                    tt = self.tokens[self.i]
                    if tt.type == "list_item_open":
                        depth += 1
                    elif tt.type == "list_item_close":
                        depth -= 1
                        if depth == 0:
                            self.i += 1
                            break
                    body.append(tt)
                    self.i += 1
                items.append(body)
            else:
                self.i += 1
        parts = ["<ol>"]
        for body in items:
            parts.append("<li>" + _render_list_item(body) + "</li>")
        parts.append("</ol>")
        self.out.append("".join(parts))

    # -------------------------------------------------------------- tables
    def _do_table_open(self, tok: Token) -> None:
        self.i += 1
        header_rows: List[List[str]] = []
        body_rows: List[List[str]] = []
        in_thead = False
        in_tbody = False
        while self.i < len(self.tokens):
            t = self.tokens[self.i]
            if t.type == "table_close":
                self.i += 1
                break
            if t.type == "thead_open":
                in_thead, in_tbody = True, False
                self.i += 1
                continue
            if t.type == "thead_close":
                in_thead = False
                self.i += 1
                continue
            if t.type == "tbody_open":
                in_tbody, in_thead = True, False
                self.i += 1
                continue
            if t.type == "tbody_close":
                in_tbody = False
                self.i += 1
                continue
            if t.type == "tr_open":
                self.i += 1
                row: List[str] = []
                while self.i < len(self.tokens):
                    tt = self.tokens[self.i]
                    if tt.type == "tr_close":
                        self.i += 1
                        break
                    if tt.type in ("td_open", "th_open"):
                        self.i += 1
                        inline = self.tokens[self.i]
                        self.i += 1  # inline
                        self.i += 1  # td_close / th_close
                        row.append(_render_inline(inline.children or []))
                    else:
                        self.i += 1
                if in_thead:
                    header_rows.append(row)
                else:
                    body_rows.append(row)
                continue
            self.i += 1

        header = header_rows[0] if header_rows else []
        parts = ["<table>"]
        if header:
            parts.append("<thead><tr>")
            for cell in header:
                parts.append(f"<th>{cell}</th>")
            parts.append("</tr></thead>")
        parts.append("<tbody>")
        for row in body_rows:
            parts.append("<tr>")
            for cell in row:
                parts.append(f"<td>{cell}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")
        self.out.append("".join(parts))

    # ---------------------------------------------------------- raw html
    def _do_html_block(self, tok: Token) -> None:
        self.i += 1
        content = tok.content.strip()

        # Paragraph alignment wrapper.
        align = _ALIGN_P_RE.match(content)
        if align and align.group("align").lower() in SAFE_ALIGN_VALUES:
            body = align.group("body").strip()
            rendered = _render_inline_html_body(body)
            self.out.append(
                f'<p style="text-align: {align.group("align").lower()}">{rendered}</p>'
            )
            return

        # Bare iframe on its own line/block – wrap in html-bobswift.
        if _IFRAME_BLOCK_RE.match(content):
            attrs = parse_iframe_markup(content) or {}
            rendered = render_iframe(attrs)
            if not rendered:
                return
            safe_body = rendered.replace("]]>", "]]]]><![CDATA[>")
            self.out.append(
                '<ac:structured-macro ac:name="html-bobswift">'
                f"<ac:plain-text-body><![CDATA[{safe_body}]]></ac:plain-text-body>"
                "</ac:structured-macro>"
            )
            return

        # Iframe(s) embedded inside a larger html_block – e.g. the
        # user didn't leave blank lines around the embed, so
        # markdown-it rolled the iframe together with neighbouring
        # text into one html_block.  Confluence storage format does
        # not allow raw ``<iframe>``, so we still have to extract
        # each iframe and wrap it in an ``html-bobswift`` macro.
        # Non-iframe fragments are emitted verbatim (same fallback
        # as the catch-all branch below).
        if _IFRAME_SCAN_RE.search(content):
            cursor = 0
            emitted_any = False
            for match in _IFRAME_SCAN_RE.finditer(content):
                before = content[cursor:match.start()]
                if before.strip():
                    self.out.append(before)
                    emitted_any = True
                cursor = match.end()
                attrs = parse_iframe_markup(match.group(0)) or {}
                rendered = render_iframe(attrs)
                if not rendered:
                    # Unsafe iframe – drop it entirely; surrounding
                    # fragments have already been preserved above.
                    continue
                safe_body = rendered.replace("]]>", "]]]]><![CDATA[>")
                self.out.append(
                    '<ac:structured-macro ac:name="html-bobswift">'
                    f"<ac:plain-text-body><![CDATA[{safe_body}]]></ac:plain-text-body>"
                    "</ac:structured-macro>"
                )
                emitted_any = True
            tail = content[cursor:]
            if tail.strip():
                self.out.append(tail)
                emitted_any = True
            if emitted_any:
                return

        # Unknown-macro placeholders that happened to land as their own
        # block paragraph (HTML comments).  They'll be restored later.
        if _UNKNOWN_MACRO_PLACEHOLDER_RE.match(content):
            self.out.append(content)
            return

        # Everything else: pass through verbatim – this covers bare
        # top-level HTML the user wrote (e.g. complex widgets we don't
        # understand).  Unknown tags are still wrapped in <p> by the
        # markdown-it block rules, so there's nothing extra to do here.
        self.out.append(content)

    def _do_heading_close(self, tok: Token) -> None:      self.i += 1
    def _do_paragraph_close(self, tok: Token) -> None:    self.i += 1
    def _do_inline(self, tok: Token) -> None:             self.i += 1


# ---------------------------------------------------------------------------
# Inline helpers
# ---------------------------------------------------------------------------


def _render_inline(children: List[Token]) -> str:
    """Render a sequence of inline tokens to storage-format HTML."""

    buf: List[str] = []
    i = 0
    while i < len(children):
        t = children[i]
        ty = t.type
        if ty == "text":
            buf.append(html.escape(t.content))
        elif ty == "softbreak":
            buf.append("<br/>")
        elif ty == "hardbreak":
            buf.append("<br/>")
        elif ty == "code_inline":
            buf.append(f"<code>{html.escape(t.content)}</code>")
        elif ty == "strong_open":
            buf.append("<strong>")
        elif ty == "strong_close":
            buf.append("</strong>")
        elif ty == "em_open":
            buf.append("<em>")
        elif ty == "em_close":
            buf.append("</em>")
        elif ty == "s_open":
            buf.append("<s>")
        elif ty == "s_close":
            buf.append("</s>")
        elif ty == "link_open":
            href = dict(t.attrs or {}).get("href", "")
            # Attachment-backed link when the pull side marked it, or when
            # the href is a relative path that lives under the attachments
            # directory (``attachments/foo.pdf``).  The close token is
            # consumed by the helper so we don't emit an ``</a>`` later.
            marker_index = _find_attachment_marker(children, i)
            is_attachment = marker_index is not None or _is_local_attachment_path(href)
            if is_attachment:
                consumed = _emit_attachment_link(buf, children, i, href, marker_index)
                i = consumed
                continue
            buf.append(f'<a href="{html.escape(href, quote=True)}">')
        elif ty == "link_close":
            buf.append("</a>")
        elif ty == "image":
            attrs = dict(t.attrs or {})
            src = attrs.get("src", "")
            alt_text = "".join(_plain_text(c) for c in (t.children or []))
            # An optional ``<!--cm-image …-->`` comment immediately after
            # the image carries width / height / align that Markdown can
            # not express natively.
            extra_attrs, skip = _consume_image_attr_comment(children, i + 1)
            buf.append(_render_image(src, alt_text, extra_attrs))
            i += skip
        elif ty == "html_inline":
            buf.append(_sanitise_html_inline(children, i, buf))
            # _sanitise_html_inline may consume trailing tokens (e.g. span
            # with unsafe style is dropped together with its close tag).
            # It signals via the return value that we should advance.
            # We always advance by 1 here since the function only inspects
            # this one token; span pairs are handled by emitting tokens.
        else:
            # Unknown – ignore.
            pass
        i += 1
    return "".join(buf)


def _plain_text(tok: Token) -> str:
    if tok.type == "text":
        return tok.content
    if tok.type in ("softbreak", "hardbreak"):
        return " "
    if tok.children:
        return "".join(_plain_text(c) for c in tok.children)
    return ""


def _normalise_code_language(language: str) -> str:
    return _CODE_LANGUAGE_FALLBACKS.get(language.lower(), language)


# ---------------------------------------------------------------------------
# Image / attachment helpers
# ---------------------------------------------------------------------------


_EXTERNAL_URL_RE = re.compile(r"^[a-zA-Z][\w+.-]*:|^//|^#", re.ASCII)


def _is_external_url(src: str) -> bool:
    """Return True when ``src`` looks like an http(s)/data/mailto URL etc."""

    return bool(src and _EXTERNAL_URL_RE.match(src))


def _is_local_attachment_path(href: str) -> bool:
    """Heuristic for links that point at a file stored as an attachment.

    We only treat relative paths living under ``attachments/`` as
    attachments; other relative paths may be wiki page references so we
    leave them as ordinary ``<a href>`` to avoid false positives.
    """

    if not href or _is_external_url(href):
        return False
    normalized = href.replace("\\", "/").lstrip("./")
    return normalized.startswith(ATTACHMENTS_DIRNAME + "/")


def _consume_image_attr_comment(
    children: List[Token], start: int
) -> Tuple[Dict[str, str], int]:
    """If the next inline token is a ``<!--cm-image …-->`` marker, parse it.

    Returns ``(attrs_dict, tokens_consumed_beyond_the_image)``.
    """

    j = start
    # Allow a single whitespace-only text token between image and comment.
    if j < len(children) and children[j].type == "text" and not children[j].content.strip():
        j += 1
    if j < len(children) and children[j].type == "html_inline":
        attrs = parse_image_attr_comment(children[j].content)
        if attrs:
            return attrs, j - start + 1
    return {}, 0


def _find_attachment_marker(children: List[Token], link_open_index: int) -> Optional[int]:
    """Return the index of a ``<!--cm-attachment-->`` marker following the link.

    Scans forward from ``link_open_index`` looking for the matching
    ``link_close`` and, immediately after, an ``html_inline`` token whose
    content is the attachment marker.  Returns ``None`` when no marker is
    present; otherwise the index of the marker token.
    """

    depth = 0
    j = link_open_index
    while j < len(children):
        tok = children[j]
        if tok.type == "link_open":
            depth += 1
        elif tok.type == "link_close":
            depth -= 1
            if depth == 0:
                k = j + 1
                if (
                    k < len(children)
                    and children[k].type == "text"
                    and not children[k].content.strip()
                ):
                    k += 1
                if (
                    k < len(children)
                    and children[k].type == "html_inline"
                    and ATTACHMENT_LINK_MARKER_RE.match(children[k].content or "")
                ):
                    return k
                return None
        j += 1
    return None


def _emit_attachment_link(
    buf: List[str],
    children: List[Token],
    link_open_index: int,
    href: str,
    marker_index: Optional[int],
) -> int:
    """Emit a ``<ac:link><ri:attachment/>…`` and return the new cursor.

    Consumes tokens up to and including the ``link_close`` (and the
    trailing marker when present) so the outer loop can skip past them.
    """

    # Collect the label text from the tokens between link_open and link_close.
    label_parts: List[str] = []
    depth = 0
    j = link_open_index
    close_index = link_open_index
    while j < len(children):
        tok = children[j]
        if tok.type == "link_open":
            depth += 1
        elif tok.type == "link_close":
            depth -= 1
            if depth == 0:
                close_index = j
                break
        else:
            if depth >= 1:
                label_parts.append(_plain_text(tok))
        j += 1

    label = "".join(label_parts).strip()
    filename = sanitize_attachment_filename(os.path.basename(href.replace("\\", "/")))
    safe_label = (label or filename).replace("]]>", "]]]]><![CDATA[>")
    buf.append(
        f'<ac:link><ri:attachment ri:filename="{html.escape(filename, quote=True)}" />'
        f"<ac:plain-text-link-body><![CDATA[{safe_label}]]></ac:plain-text-link-body>"
        "</ac:link>"
    )
    # Advance past link_close and an optional marker/text-whitespace pair.
    new_i = close_index + 1
    if marker_index is not None and marker_index >= new_i:
        new_i = marker_index + 1
    return new_i


def _render_image(src: str, alt: str, extra: Dict[str, str]) -> str:
    """Render a Markdown image as a Confluence ``<ac:image>`` element.

    * Absolute URLs keep the historical ``<ri:url>`` child.
    * Relative paths are rewritten to ``<ri:attachment ri:filename="…"/>``.
    * ``extra`` may contain ``width`` / ``height`` / ``align`` etc.
      preserved from the pull side.
    """

    attr_parts: List[str] = []
    if alt:
        attr_parts.append(f'ac:alt="{html.escape(alt, quote=True)}"')
    # Preserve a small allow-list of dimension / layout attributes.
    for key in ("title", "width", "height", "align", "layout", "thumbnail", "border", "class", "style"):
        value = extra.get(key)
        if not value:
            continue
        attr_parts.append(f'ac:{key}="{html.escape(value, quote=True)}"')
    attrs_blob = (" " + " ".join(attr_parts)) if attr_parts else ""

    if _is_external_url(src):
        title = html.escape(alt, quote=True)
        url = html.escape(src, quote=True)
        return (
            f"<ac:image{attrs_blob}>"
            f'<ri:url ri:value="{url}" ri:title="{title}" />'
            "</ac:image>"
        )

    basename = os.path.basename(src.replace("\\", "/")) if src else ""
    filename = sanitize_attachment_filename(basename) if basename else ""
    if not filename:
        # No resolvable target – fall back to an empty image so we don't
        # crash; the user can fix the source in the editor.
        return f"<ac:image{attrs_blob}></ac:image>"
    return (
        f"<ac:image{attrs_blob}>"
        f'<ri:attachment ri:filename="{html.escape(filename, quote=True)}" />'
        "</ac:image>"
    )


def _sanitise_html_inline(
    children: List[Token], index: int, buf: List[str]
) -> str:
    """Return the sanitised / escaped form of ``children[index]``.

    Passthrough tags (`u`, `s`, `sub`, `sup`, …), `<br>`, and
    ``<span style="safe">`` wrappers are emitted verbatim (with the style
    attribute filtered).  Anything else is HTML-escaped so raw tag text
    survives as literal characters rather than silently executing as
    markup.
    """

    raw = children[index].content
    if _BR_RE.match(raw):
        return "<br/>"
    if IMAGE_ATTR_COMMENT_RE.match(raw or ""):
        # Image-attribute marker left over when the comment was not
        # adjacent to an image token (e.g. because the user edited the
        # file and removed the image).  Drop it silently.
        return ""
    if ATTACHMENT_LINK_MARKER_RE.match(raw or ""):
        # Similar story for orphaned attachment-link markers.
        return ""
    if _INLINE_HTML_PASSTHROUGH.match(raw):
        return raw.lower()
    span = _SPAN_OPEN_RE.match(raw)
    if span:
        safe = _sanitise_inline_style(span.group("style"))
        if safe:
            return f'<span style="{html.escape(safe, quote=True)}">'
        return ""
    if raw.lower() in ("</span>",):
        # Emit the closer only if we have an unmatched open in buf.
        joined = "".join(buf)
        if joined.count("<span ") > joined.count("</span>"):
            return "</span>"
        return ""
    # Unknown inline HTML – drop the tag but keep any visible characters
    # by escaping.  This preserves the text of crafted input like
    # ``<img onerror=…>`` without letting it become live markup.
    return html.escape(raw)


def _sanitise_inline_style(style: str) -> str:
    parts: List[str] = []
    for decl in style.split(";"):
        if ":" not in decl:
            continue
        prop, _, value = decl.partition(":")
        prop = prop.strip().lower()
        value = value.strip()
        if prop not in ("color", "background-color"):
            continue
        from ._style import SAFE_COLOR_RE
        if not SAFE_COLOR_RE.match(value):
            continue
        parts.append(f"{prop}: {value}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# List / blockquote helpers
# ---------------------------------------------------------------------------


def _list_item_raw_text(body: List[Token]) -> str:
    """Plain-text representation of the first paragraph inside a list item.

    Used for task-list marker detection so we keep the behaviour of the
    legacy renderer (only convert when *every* item is a task).
    """

    for i, t in enumerate(body):
        if t.type == "paragraph_open" and i + 1 < len(body):
            inline = body[i + 1]
            if inline.type == "inline":
                return "".join(_plain_text(c) for c in (inline.children or []))
    return ""


def _render_list_item(body: List[Token]) -> str:
    """Render the body tokens of a single ``<li>`` to storage XML.

    A list item may contain multiple paragraphs and/or nested lists.
    Single-paragraph items render the inline content directly (without a
    wrapping ``<p>``) to match the shape ``storage_to_markdown`` emits
    and keep the existing tests happy.
    """

    # Shortcut: exactly one paragraph → emit its inline content.
    if (
        len(body) == 3
        and body[0].type == "paragraph_open"
        and body[1].type == "inline"
        and body[2].type == "paragraph_close"
    ):
        return _render_inline(body[1].children or [])

    # Otherwise render via a sub-renderer but then unwrap a single
    # leading ``<p>…</p>`` (for items that are "paragraph + nested list").
    sub = _BlockRenderer(body)
    rendered = sub.render()
    rendered = re.sub(r"^<p>(.*?)</p>", r"\1", rendered, count=1, flags=re.DOTALL)
    return rendered


def _detect_admonition(tokens: List[Token]) -> Optional[str]:
    """If the first paragraph is a ``[!NAME]`` header, mutate ``tokens``
    in place to remove it and return ``"info"`` / ``"note"`` / etc.
    """

    if not tokens:
        return None
    # Find first paragraph.
    for i in range(len(tokens)):
        t = tokens[i]
        if t.type != "paragraph_open":
            continue
        if i + 2 >= len(tokens):
            return None
        inline = tokens[i + 1]
        if inline.type != "inline":
            return None
        content_lines: List[str] = []
        # Rebuild into newline-separated lines so ``[!INFO]\nbody`` can be
        # recognised even though markdown-it represents soft breaks as a
        # distinct child rather than a literal ``\n``.
        cur = ""
        for c in (inline.children or []):
            if c.type in ("softbreak", "hardbreak"):
                content_lines.append(cur)
                cur = ""
            elif c.type == "text":
                cur += c.content
            else:
                # Any non-text inline token means we're past the header.
                cur += ""
        content_lines.append(cur)
        if not content_lines:
            return None
        match = _ADMONITION_HEADER_RE.match(content_lines[0].strip())
        if not match:
            return None
        name = match.group("name").lower()
        rest = "\n".join(content_lines[1:]).strip()
        if rest:
            inline.content = rest
            inline.children = [_make_text_token(rest)]
        else:
            del tokens[i:i + 3]
        return name
    return None


def _make_text_token(text: str) -> Token:
    tok = Token("text", "", 0)
    tok.content = text
    return tok


_TASK_MARKER_STRIP_RE = re.compile(r"^\[([ xX])\]\s+")


def _strip_leading_task_marker(body_xml: str) -> str:
    return _TASK_MARKER_STRIP_RE.sub("", body_xml, count=1)


def _render_inline_html_body(body: str) -> str:
    """Render the HTML inside a ``<p style="text-align:…">…</p>`` block.

    The body is already HTML from the perspective of Markdown (it was
    pass-through markup), so we emit it verbatim.  We still sanitise any
    ``<span style="…">`` wrappers the body contains so attribute policy
    is enforced the same way as in paragraph bodies.
    """

    return _sanitise_html_fragment(body)


def _sanitise_html_fragment(fragment: str) -> str:
    """Filter a raw inline HTML fragment the same way ``_sanitise_html_inline``
    would filter a sequence of ``html_inline`` tokens.
    """

    # Simple tag-by-tag walk: find each tag, sanitise if it's a span,
    # drop unknown tags, keep text in between.  This intentionally mirrors
    # the regex strategy the old implementation used for the analogous
    # case, but scoped to just the aligned-paragraph body.
    out: List[str] = []
    pos = 0
    for match in re.finditer(r"<[^>]+>", fragment):
        start, end = match.span()
        if start > pos:
            out.append(fragment[pos:start])
        tag = match.group(0)
        if _BR_RE.match(tag):
            out.append("<br/>")
        elif _INLINE_HTML_PASSTHROUGH.match(tag):
            out.append(tag.lower())
        else:
            span = _SPAN_OPEN_RE.match(tag)
            if span:
                safe = _sanitise_inline_style(span.group("style"))
                if safe:
                    out.append(f'<span style="{html.escape(safe, quote=True)}">')
            elif tag.lower() == "</span>":
                joined = "".join(out)
                if joined.count("<span ") > joined.count("</span>"):
                    out.append("</span>")
            # else: drop the tag entirely.
        pos = end
    if pos < len(fragment):
        out.append(fragment[pos:])
    return "".join(out)
