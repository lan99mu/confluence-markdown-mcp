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

from ._style import SAFE_ALIGN_VALUES, SAFE_COLOR_RE

# --- iframe-specific style allow-list ----------------------------------
#
# ``_style.build_span_style`` is intentionally restrictive — it is meant
# for inline ``<span>`` wrappers.  Iframes, however, legitimately carry
# layout CSS such as ``border``, ``max-width``, ``display: block``, or
# ``margin: 0 auto``.  The generic sanitiser strips all of those, which
# caused user-authored iframe ``style`` attributes to disappear after a
# push.  We keep a slightly broader, but still tightly validated,
# allow-list here.
#
# A declaration is kept only if **both** the property name is in
# ``_IFRAME_STYLE_PROPS`` **and** its value matches ``_SAFE_STYLE_VALUE_RE``
# (printable ASCII, no parentheses, no ``@``/``\\``/``;``/``"``/``'``, no
# ``url(...)``/``expression(...)`` payloads, no newlines).  Colour-bearing
# properties additionally go through the stricter ``SAFE_COLOR_RE`` from
# ``_style`` so crafted values cannot smuggle arbitrary tokens.
_IFRAME_STYLE_PROPS = frozenset({
    "width", "max-width", "min-width",
    "height", "max-height", "min-height",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "border", "border-width", "border-style", "border-radius",
    "border-top", "border-right", "border-bottom", "border-left",
    "display", "float", "clear", "vertical-align", "text-align",
    "background", "background-color", "border-color", "color",
    "box-sizing", "overflow",
})
_IFRAME_COLOR_PROPS = frozenset({
    "color", "background-color", "border-color", "background",
})
_SAFE_STYLE_VALUE_RE = re.compile(
    r"^[A-Za-z0-9 #,.\-%_/]+$"
)


def _sanitise_iframe_style(style: str) -> str:
    """Return an iframe-safe ``style`` value composed of allow-listed
    declarations, or ``""`` if nothing safe remains.
    """

    if not style:
        return ""
    kept: list[str] = []
    for raw in style.split(";"):
        if ":" not in raw:
            continue
        prop, _, value = raw.partition(":")
        prop = prop.strip().lower()
        value = value.strip()
        if not prop or not value:
            continue
        if prop not in _IFRAME_STYLE_PROPS:
            continue
        if not _SAFE_STYLE_VALUE_RE.match(value):
            continue
        if prop in _IFRAME_COLOR_PROPS:
            if not SAFE_COLOR_RE.match(value):
                continue
        if prop == "text-align":
            if value.lower() not in SAFE_ALIGN_VALUES:
                continue
            value = value.lower()
        kept.append(f"{prop}: {value}")
    return "; ".join(kept)

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
            value = _sanitise_iframe_style(value)
            if not value:
                continue
        elif name in ("width", "height"):
            stripped = value.strip()
            if not _SAFE_DIMENSION_RE.match(stripped):
                continue
            value = stripped
        parts.append(f'{name}="{_escape_attr(value)}"')
    return f"<iframe {' '.join(parts)}></iframe>"
