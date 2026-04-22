"""Shared ``<iframe>`` sanitisation used by both directions.

Confluence does not model raw ``<iframe>`` in storage format — embeds are
wrapped in an ``html`` / ``html-bobswift`` macro with a CDATA body.  On
pull we surface the iframe as Markdown-embedded HTML; on push we wrap it
back in the macro.  Either way the attribute set is filtered against a
strict allow-list and the ``src`` must be ``http`` / ``https`` /
protocol-relative so we cannot smuggle ``javascript:`` URLs through.
"""

from __future__ import annotations

import html
import re
from typing import Dict, Optional

from ._style import build_span_style, extract_align

SAFE_ATTRS = (
    "src",
    "width",
    "height",
    "frameborder",
    "allowfullscreen",
    "allow",
    "title",
    "name",
    "scrolling",
    "style",
)

SAFE_SRC_RE = re.compile(r"(?i)^(?:https?:)?//[^\s\"'<>]+$|^https?://[^\s\"'<>]+$")
_SAFE_DIMENSION_RE = re.compile(r"^\d+(?:\.\d+)?(?:px|%)?$")

_IFRAME_ATTR_RE = re.compile(
    r'(?P<name>[a-zA-Z_:][-a-zA-Z0-9_:.]*)'
    r'(?:\s*=\s*(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\'|(?P<bare>[^\s"\'>]+)))?'
)
_IFRAME_TAG_RE = re.compile(
    r"(?is)^<iframe\b(?P<body>.*?)/?\s*>\s*(?:</iframe>)?\s*$"
)


def parse_iframe_markup(markup: str) -> Optional[Dict[str, str]]:
    """Parse attributes out of an ``<iframe ...>`` opening tag (or None)."""

    match = _IFRAME_TAG_RE.match(markup.strip())
    if not match:
        return None
    attrs: Dict[str, str] = {}
    for attr_match in _IFRAME_ATTR_RE.finditer(match.group("body")):
        name = attr_match.group("name").lower()
        if attr_match.group("dq") is not None:
            value = attr_match.group("dq")
        elif attr_match.group("sq") is not None:
            value = attr_match.group("sq")
        elif attr_match.group("bare") is not None:
            value = attr_match.group("bare")
        else:
            value = ""
        attrs[name] = html.unescape(value)
    return attrs


def _escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def render_iframe(attrs: Dict[str, Optional[str]]) -> str:
    """Serialise a sanitised ``<iframe ...></iframe>`` from ``attrs``.

    Returns ``""`` if the ``src`` is missing or uses an unsafe scheme.
    """

    src = (attrs.get("src") or "").strip() if attrs else ""
    if not src or not SAFE_SRC_RE.match(src):
        return ""

    parts = [f'src="{_escape_attr(src)}"']
    for name in SAFE_ATTRS:
        if name == "src" or name not in attrs:
            continue
        value = attrs[name]
        if name == "allowfullscreen":
            parts.append("allowfullscreen")
            continue
        if value is None:
            continue
        value = str(value)
        if name == "style":
            sanitised = build_span_style(value)
            align = extract_align(value)
            pieces = []
            if sanitised:
                pieces.append(sanitised)
            if align:
                pieces.append(f"text-align: {align}")
            if not pieces:
                continue
            value = "; ".join(pieces)
        elif name in ("width", "height"):
            stripped = value.strip()
            if not _SAFE_DIMENSION_RE.match(stripped):
                continue
            value = stripped
        parts.append(f'{name}="{_escape_attr(value)}"')
    return f"<iframe {' '.join(parts)}></iframe>"
