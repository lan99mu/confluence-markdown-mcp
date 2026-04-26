"""Handling of Confluence storage-format *macros* (``<ac:structured-macro>``).

Confluence stores rich content as XHTML plus a handful of custom XML
elements.  Two macros receive first-class support because they round-trip
cleanly to Markdown:

* ``code`` – fenced code block with language hint.
* ``info`` / ``note`` / ``warning`` / ``tip`` – admonition panels, rendered
  as Markdown blockquotes prefixed with ``> [!INFO]``-style labels.

In addition this module rewrites ``<ac:image>`` and ``<ac:link>`` elements
that reference ``<ri:attachment>`` / ``<ri:url>`` children into plain
Markdown image / link syntax – otherwise lxml's HTML parser would silently
drop the ``ri:*`` children and we would lose the filename.  Extra image
attributes (width / height / align) are preserved via a compact HTML
comment marker so a pull → edit → push round trip does not silently
discard them.

Any other structured macro (jira issue, etc.) is preserved verbatim as an
HTML comment token so round-tripping still does not lose content.
"""

from __future__ import annotations

import html
import os
import re
from typing import Iterable, List, Set, Tuple

from ._iframe import parse_iframe_markup
from ._plantuml import decode_plantuml_url

# ---- Recognised admonition macros ---------------------------------------
ADMONITIONS = ("info", "note", "warning", "tip")
ADMONITION_LABELS = {
    "info": "INFO",
    "note": "NOTE",
    "warning": "WARNING",
    "tip": "TIP",
}

_MACRO_RE = re.compile(
    r"<ac:structured-macro\b[^>]*?\bac:name\s*=\s*\"(?P<name>[^\"]+)\"[^>]*>"
    r"(?P<body>.*?)"
    r"</ac:structured-macro>",
    re.DOTALL,
)

# Confluence task lists are *not* wrapped in ``<ac:structured-macro>`` – they
# are first-class elements.  We rewrite them into a plain ``<ul>`` with
# ``[ ]`` / ``[x]`` task markers so the HTML parser can handle them
# naturally; otherwise the ``<ac:task-id>`` / ``<ac:task-status>`` children
# leak through as bare text ("9 incomplete 有，…").
_TASK_LIST_RE = re.compile(r"<ac:task-list\b[^>]*>(?P<body>.*?)</ac:task-list>", re.DOTALL)
_TASK_RE = re.compile(r"<ac:task\b[^>]*>(?P<body>.*?)</ac:task>", re.DOTALL)
_TASK_STATUS_RE = re.compile(
    r"<ac:task-status\b[^>]*>(?P<status>[^<]*)</ac:task-status>",
    re.DOTALL,
)
_TASK_BODY_RE = re.compile(
    r"<ac:task-body\b[^>]*>(?P<body>.*?)</ac:task-body>",
    re.DOTALL,
)
_TASK_ID_RE = re.compile(
    r"<ac:task-id\b[^>]*>[^<]*</ac:task-id>",
    re.DOTALL,
)

_LANG_RE = re.compile(
    r"<ac:parameter\b[^>]*\bac:name\s*=\s*\"language\"[^>]*>(?P<lang>[^<]*)"
    r"</ac:parameter>",
    re.DOTALL,
)
_PLAIN_BODY_RE = re.compile(
    r"<ac:plain-text-body>\s*(?:<!\[CDATA\[)?(?P<text>.*?)(?:\]\]>)?\s*</ac:plain-text-body>",
    re.DOTALL,
)
_RICH_BODY_RE = re.compile(
    r"<ac:rich-text-body>(?P<text>.*?)</ac:rich-text-body>",
    re.DOTALL,
)

# Use Private-Use-Area sentinel characters as placeholders so they
# survive both ``html.parser.HTMLParser`` and ``lxml``'s HTML parser
# (lxml turns ``\0`` into U+FFFD).  U+E000 / U+E001 are not assigned to
# any real character, so the chance of collision with page content is
# effectively zero.
PLACEHOLDER_PREFIX = "\ue000MACRO"
_PLACEHOLDER_END = "\ue001"
PLACEHOLDER_RE = re.compile(r"\ue000MACRO(\d+)\ue001")


def preprocess_storage(storage_html: str) -> Tuple[str, List[str]]:
    """Replace macros with tokens so the downstream HTML parser is safe.

    Returns ``(processed_html, replacements)``.  ``processed_html`` contains
    only standard-ish XHTML (no ``<ac:*>`` tags, no CDATA sections).  Each
    token ``\\0MACROi\\0`` must later be replaced with ``replacements[i]``
    (already-formatted Markdown) via :func:`postprocess_markdown`.
    """

    replacements: List[str] = []

    def _replace(match: "re.Match[str]") -> str:
        name = match.group("name").lower()
        body = match.group("body") or ""
        snippet = _render_macro(name, body)
        replacements.append(snippet)
        return f"{PLACEHOLDER_PREFIX}{len(replacements) - 1}{_PLACEHOLDER_END}"

    processed = _rewrite_task_lists(storage_html)
    processed = _rewrite_ac_images(processed, replacements)
    processed = _rewrite_ac_attachment_links(processed, replacements)
    processed = _MACRO_RE.sub(_replace, processed)
    return processed, replacements


def postprocess_markdown(markdown_text: str, replacements: List[str]) -> str:
    """Replace macro placeholder tokens with their rendered Markdown."""

    def _sub(match: "re.Match[str]") -> str:
        idx = int(match.group(1))
        if 0 <= idx < len(replacements):
            return replacements[idx]
        return ""

    return PLACEHOLDER_RE.sub(_sub, markdown_text)


# ------------------------------------------------------------------ helpers


def _rewrite_task_lists(storage_html: str) -> str:
    """Turn ``<ac:task-list>`` blocks into plain ``<ul>`` markup.

    Each ``<ac:task>`` becomes ``<li>[ ] body</li>`` (incomplete) or
    ``<li>[x] body</li>`` (complete).  ``<ac:task-id>`` elements are dropped
    because the IDs are not meaningful once the page is edited offline.
    Nested task lists are handled naturally because the regex is applied
    iteratively (innermost first, thanks to non-greedy matching of siblings
    that do not themselves contain ``</ac:task-list>``).
    """

    def _render_task(match: "re.Match[str]") -> str:
        body = match.group("body") or ""
        status = ""
        status_match = _TASK_STATUS_RE.search(body)
        if status_match:
            status = status_match.group("status").strip().lower()
        body_match = _TASK_BODY_RE.search(body)
        inner = body_match.group("body") if body_match else ""
        mark = "[x]" if status == "complete" else "[ ]"
        return f"<li>{mark} {inner}</li>"

    def _render_task_list(match: "re.Match[str]") -> str:
        inner = match.group("body") or ""
        inner = _TASK_ID_RE.sub("", inner)
        inner = _TASK_RE.sub(_render_task, inner)
        return f"<ul>{inner}</ul>"

    # Iterate so that nested ``<ac:task-list>`` entries are rewritten too.
    current = storage_html
    while True:
        current, count = _TASK_LIST_RE.subn(_render_task_list, current)
        if not count:
            break
    return current


def _render_macro(name: str, body: str) -> str:
    if name == "code":
        return _render_code_macro(body)
    if name in ADMONITIONS:
        return _render_admonition_macro(name, body)
    if name == "html" or name == "html-bobswift":
        return _render_html_macro(body)
    return _render_unknown_macro(name, body)


def _render_html_macro(body: str) -> str:
    """Expand an ``html`` / ``html-bobswift`` macro to its raw HTML body.

    The macro's ``<ac:plain-text-body>`` is a CDATA block of literal HTML
    (most commonly an ``<iframe>`` embed for drawio / diagrams.net).  We
    splice the HTML back into the stream so the downstream parser can
    render it naturally — an ``<iframe>`` inside becomes a Markdown
    iframe line, links become Markdown links, etc.
    """

    text_match = _PLAIN_BODY_RE.search(body)
    raw = text_match.group("text") if text_match else ""
    raw = raw.strip()
    if not raw:
        return ""
    attrs = parse_iframe_markup(raw)
    if attrs:
        plantuml = decode_plantuml_url(attrs.get("src", ""))
        if plantuml is not None:
            return f"\n\n```plantuml\n{plantuml.rstrip()}\n```\n\n"
    # Preserve surrounding blank lines so the embed is treated as its
    # own block when the HTML parser sees it.
    return f"\n\n{raw}\n\n"


def _render_code_macro(body: str) -> str:
    lang_match = _LANG_RE.search(body)
    text_match = _PLAIN_BODY_RE.search(body)
    language = lang_match.group("lang").strip() if lang_match else ""
    raw = text_match.group("text") if text_match else ""
    # CDATA contents are not HTML-escaped; emit verbatim.
    return f"\n\n```{language}\n{raw.rstrip()}\n```\n\n"


def _render_admonition_macro(name: str, body: str) -> str:
    """Convert an admonition macro to a GFM-style blockquote.

    The inner rich-text-body is run through a late-bound importer of
    :mod:`storage_to_md` so we can reuse the full HTML-to-Markdown pipeline
    recursively.  The import is deferred to avoid a module-level cycle.
    """

    from .storage_to_md import storage_to_markdown  # local import on purpose

    rich = _RICH_BODY_RE.search(body)
    inner_html = rich.group("text") if rich else body
    inner_md = storage_to_markdown(inner_html).strip()

    label = ADMONITION_LABELS[name]
    quoted = "\n".join(f"> {line}" if line else ">" for line in inner_md.splitlines())
    return f"\n\n> [!{label}]\n{quoted}\n\n"


def _render_unknown_macro(name: str, body: str) -> str:
    # Preserve unknown macros as an HTML comment so they survive round
    # trips.  md_to_storage.py knows how to detect and re-emit these.
    escaped = html.escape(body, quote=False)
    return (
        f"\n\n<!--confluence-macro name=\"{html.escape(name, quote=True)}\">"
        f"{escaped}<!--/confluence-macro-->\n\n"
    )


# Used by md_to_storage to re-emit unknown macros on upload.
UNKNOWN_MACRO_RE = re.compile(
    r"<!--confluence-macro name=\"(?P<name>[^\"]+)\">"
    r"(?P<body>.*?)"
    r"<!--/confluence-macro-->",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Images & attachment-aware links
# ---------------------------------------------------------------------------

# Default subdirectory (relative to the Markdown file) where pulled
# attachments are stored and where md_to_storage looks when deciding
# whether a local image / link should become an ``<ri:attachment>``.
ATTACHMENTS_DIRNAME = "attachments"

# Marker comments used after Markdown image / link syntax to carry
# extra information that has no native Markdown representation.  Kept
# terse so they don't dominate the rendered file visually.
_IMG_ATTR_COMMENT_RE = re.compile(
    r"<!--\s*cm-image(?P<attrs>(?:\s+[a-zA-Z][\w-]*=\"[^\"]*\")*)\s*-->"
)
_ATTACHMENT_LINK_MARKER = "<!--cm-attachment-->"
_ATTACHMENT_LINK_MARKER_RE = re.compile(r"<!--\s*cm-attachment\s*-->")

# Matches a Confluence ``<ac:image>`` block and captures its attributes
# and body so we can rewrite both ``<ri:attachment>`` and ``<ri:url>``
# children.  HTML parsing is not safe here because lxml silently drops
# the ``ri:*`` children due to the missing namespace declaration.
_AC_IMAGE_RE = re.compile(
    r"<ac:image\b(?P<attrs>[^>]*)>(?P<body>.*?)</ac:image>",
    re.DOTALL | re.IGNORECASE,
)
_AC_IMAGE_SELF_CLOSE_RE = re.compile(
    r"<ac:image\b(?P<attrs>[^>]*)/\s*>",
    re.IGNORECASE,
)
# Attachment-backed links: ``<ac:link><ri:attachment/><ac:plain-text-link-body>…``
_AC_LINK_ATTACHMENT_RE = re.compile(
    r"<ac:link\b(?P<attrs>[^>]*)>(?P<body>.*?)</ac:link>",
    re.DOTALL | re.IGNORECASE,
)
_RI_ATTACHMENT_RE = re.compile(
    r"<ri:attachment\b(?P<attrs>[^>]*)/?\s*>(?:\s*</ri:attachment>)?",
    re.DOTALL | re.IGNORECASE,
)
_RI_URL_RE = re.compile(
    r"<ri:url\b(?P<attrs>[^>]*)/?\s*>(?:\s*</ri:url>)?",
    re.DOTALL | re.IGNORECASE,
)
_AC_PLAIN_LINK_BODY_RE = re.compile(
    r"<ac:plain-text-link-body\b[^>]*>\s*(?:<!\[CDATA\[)?(?P<text>.*?)(?:\]\]>)?\s*"
    r"</ac:plain-text-link-body>",
    re.DOTALL | re.IGNORECASE,
)
_AC_LINK_BODY_RE = re.compile(
    r"<ac:link-body\b[^>]*>(?P<text>.*?)</ac:link-body>",
    re.DOTALL | re.IGNORECASE,
)

_XML_ATTR_RE = re.compile(r"([a-zA-Z_][\w:-]*)\s*=\s*\"([^\"]*)\"")

# Characters that are unsafe or awkward on common filesystems.  We keep
# Unicode letters/numbers for readability and only strip what would
# actually cause problems.
_UNSAFE_ATTACHMENT_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _parse_xml_attrs(blob: str) -> List[Tuple[str, str]]:
    """Return a list of ``(name, html-unescaped value)`` tuples."""

    return [(m.group(1), html.unescape(m.group(2))) for m in _XML_ATTR_RE.finditer(blob or "")]


def _attr_dict(blob: str) -> "dict[str, str]":
    return {name: value for name, value in _parse_xml_attrs(blob)}


def sanitize_attachment_filename(filename: str, fallback: str = "attachment") -> str:
    """Return a filesystem-safe basename for ``filename``.

    Path components are stripped (so ``../secret`` can never escape the
    configured attachments directory), control characters and the usual
    Windows-reserved characters are replaced with spaces.
    """

    if not filename:
        return fallback
    # Only ever keep the base name – no directories, absolute or relative.
    base = os.path.basename(filename.replace("\\", "/"))
    cleaned = _UNSAFE_ATTACHMENT_RE.sub(" ", base)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.lstrip(".")  # avoid accidental hidden files
    return cleaned or fallback


def _format_image_attrs(attrs: "dict[str, str]") -> str:
    """Serialise preserved image attributes as an HTML comment.

    Only a conservative allow-list is kept – the attributes that actually
    survive on the Confluence side and whose values are short and safe.
    """

    preserved: List[Tuple[str, str]] = []
    allow = (
        "ac:alt",
        "ac:title",
        "ac:width",
        "ac:height",
        "ac:align",
        "ac:layout",
        "ac:thumbnail",
        "ac:border",
        "ac:class",
        "ac:style",
    )
    for key in allow:
        if key in attrs and attrs[key]:
            short = key.split(":", 1)[1]
            # Quote-escape the value for safe HTML-comment storage.
            value = attrs[key].replace('"', "'")
            preserved.append((short, value))
    if not preserved:
        return ""
    rendered = " ".join(f'{k}="{html.escape(v, quote=True)}"' for k, v in preserved)
    return f"<!--cm-image {rendered}-->"


def parse_image_attr_comment(comment: str) -> "dict[str, str]":
    """Parse the ``<!--cm-image …-->`` marker back into a dict.

    Returns an empty dict when the comment is malformed or missing.
    """

    match = _IMG_ATTR_COMMENT_RE.match(comment or "")
    if not match:
        return {}
    return {k: html.unescape(v) for k, v in _XML_ATTR_RE.findall(match.group("attrs") or "")}


# Public regex re-used by md_to_storage to detect and strip the marker.
IMAGE_ATTR_COMMENT_RE = _IMG_ATTR_COMMENT_RE
ATTACHMENT_LINK_MARKER = _ATTACHMENT_LINK_MARKER
ATTACHMENT_LINK_MARKER_RE = _ATTACHMENT_LINK_MARKER_RE


def _rewrite_ac_images(storage_html: str, replacements: List[str]) -> str:
    """Convert ``<ac:image>`` elements to Markdown image tokens."""

    def _render(attrs_blob: str, body: str) -> str:
        attrs = _attr_dict(attrs_blob)
        alt = attrs.get("ac:alt") or attrs.get("ac:title") or ""
        filename: str = ""
        url: str = ""
        attachment_match = _RI_ATTACHMENT_RE.search(body or "")
        if attachment_match:
            a = _attr_dict(attachment_match.group("attrs"))
            filename = a.get("ri:filename", "")
        url_match = _RI_URL_RE.search(body or "")
        if url_match:
            u = _attr_dict(url_match.group("attrs"))
            url = u.get("ri:value", "")

        target: str
        if filename:
            safe = sanitize_attachment_filename(filename)
            target = f"{ATTACHMENTS_DIRNAME}/{safe}"
        elif url:
            target = url
        else:
            # Unknown form – drop the element but keep a tiny placeholder
            # so editors can notice the missing asset.
            target = ""

        alt_escaped = alt.replace("[", r"\[").replace("]", r"\]")
        target_escaped = (target or "").replace("(", "%28").replace(")", "%29")
        snippet = f"![{alt_escaped}]({target_escaped})"
        marker = _format_image_attrs(attrs)
        if marker:
            snippet += marker
        return snippet

    def _replace_pair(match: "re.Match[str]") -> str:
        snippet = _render(match.group("attrs") or "", match.group("body") or "")
        replacements.append(snippet)
        return f"{PLACEHOLDER_PREFIX}{len(replacements) - 1}{_PLACEHOLDER_END}"

    def _replace_self(match: "re.Match[str]") -> str:
        snippet = _render(match.group("attrs") or "", "")
        replacements.append(snippet)
        return f"{PLACEHOLDER_PREFIX}{len(replacements) - 1}{_PLACEHOLDER_END}"

    processed = _AC_IMAGE_RE.sub(_replace_pair, storage_html)
    processed = _AC_IMAGE_SELF_CLOSE_RE.sub(_replace_self, processed)
    return processed


def _rewrite_ac_attachment_links(storage_html: str, replacements: List[str]) -> str:
    """Convert ``<ac:link><ri:attachment/>…</ac:link>`` to Markdown links."""

    def _render(match: "re.Match[str]") -> str:
        body = match.group("body") or ""
        attachment_match = _RI_ATTACHMENT_RE.search(body)
        if not attachment_match:
            # Not an attachment link – leave the element untouched so the
            # downstream parser can deal with page / user / anchor links.
            return match.group(0)
        a = _attr_dict(attachment_match.group("attrs"))
        filename = a.get("ri:filename", "")
        if not filename:
            return match.group(0)

        text_match = _AC_PLAIN_LINK_BODY_RE.search(body) or _AC_LINK_BODY_RE.search(body)
        # Inner body may contain markup – strip tags for the Markdown label.
        if text_match:
            raw_text = re.sub(r"<[^>]+>", "", text_match.group("text") or "")
            label = html.unescape(raw_text).strip()
        else:
            label = ""
        if not label:
            label = filename

        safe = sanitize_attachment_filename(filename)
        target = f"{ATTACHMENTS_DIRNAME}/{safe}"
        label_escaped = label.replace("[", r"\[").replace("]", r"\]")
        target_escaped = target.replace("(", "%28").replace(")", "%29")
        snippet = f"[{label_escaped}]({target_escaped}){_ATTACHMENT_LINK_MARKER}"
        replacements.append(snippet)
        return f"{PLACEHOLDER_PREFIX}{len(replacements) - 1}{_PLACEHOLDER_END}"

    return _AC_LINK_ATTACHMENT_RE.sub(_render, storage_html)


# ---------------------------------------------------------------------------
# Helpers shared with the service layer
# ---------------------------------------------------------------------------


# Match ``<ri:attachment ri:filename="…"/>`` anywhere in raw storage XML,
# regardless of whether it's inside an image, link, or a macro body.
RI_FILENAME_RE = re.compile(
    r"<ri:attachment\b[^>]*\bri:filename\s*=\s*\"(?P<name>[^\"]+)\"",
    re.IGNORECASE,
)


def iter_referenced_filenames(storage_html: str) -> Iterable[str]:
    """Yield unique ``ri:filename`` values referenced in the page body."""

    seen: Set[str] = set()
    for match in RI_FILENAME_RE.finditer(storage_html or ""):
        raw = html.unescape(match.group("name"))
        if raw and raw not in seen:
            seen.add(raw)
            yield raw
