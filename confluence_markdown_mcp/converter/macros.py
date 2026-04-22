"""Handling of Confluence storage-format *macros* (``<ac:structured-macro>``).

Confluence stores rich content as XHTML plus a handful of custom XML
elements.  Two macros receive first-class support because they round-trip
cleanly to Markdown:

* ``code`` – fenced code block with language hint.
* ``info`` / ``note`` / ``warning`` / ``tip`` – admonition panels, rendered
  as Markdown blockquotes prefixed with ``> [!INFO]``-style labels.

Any other structured macro (attachment, jira issue, etc.) is preserved
verbatim as an HTML comment token so that a pull → edit → push round trip
does not silently discard content.
"""

from __future__ import annotations

import html
import re
from typing import List, Tuple

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

PLACEHOLDER_PREFIX = "\0MACRO"
PLACEHOLDER_RE = re.compile(r"\0MACRO(\d+)\0")


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
        return f"{PLACEHOLDER_PREFIX}{len(replacements) - 1}\0"

    processed = _rewrite_task_lists(storage_html)
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

    previous = None
    current = storage_html
    # Iterate so that nested ``<ac:task-list>`` entries are rewritten too.
    while previous != current:
        previous = current
        current = _TASK_LIST_RE.sub(_render_task_list, current)
    return current


def _render_macro(name: str, body: str) -> str:
    if name == "code":
        return _render_code_macro(body)
    if name in ADMONITIONS:
        return _render_admonition_macro(name, body)
    return _render_unknown_macro(name, body)


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
