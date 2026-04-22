"""Shared style-attribute sanitisers used by both conversion directions.

Confluence embeds colour / background-colour / alignment as inline CSS on
``<span>`` / ``<p>`` / ``<h*>`` elements.  We must round-trip those
declarations so a ``pull → edit → push`` cycle preserves them, but we
must also refuse anything outside a tight allow-list so crafted input
cannot inject arbitrary CSS (or scripts via ``expression()`` /
``url(javascript:…)``) into the storage XML.
"""

from __future__ import annotations

import re
from typing import List

_COLOR_RE = re.compile(r"(?i)color\s*:\s*([^;]+?)\s*(?:;|$)")
_BG_COLOR_RE = re.compile(r"(?i)background-color\s*:\s*([^;]+?)\s*(?:;|$)")
_ALIGN_RE = re.compile(r"(?i)text-align\s*:\s*([^;]+?)\s*(?:;|$)")

SAFE_ALIGN_VALUES = {"left", "right", "center", "justify"}

# Allow-list of safe CSS colour values.  Restricting to these formats
# prevents CSS injection via crafted ``style`` attributes (e.g. smuggling
# additional declarations or ``expression()`` payloads).
SAFE_COLOR_RE = re.compile(
    r"(?ix)"
    r"^(?:"
    r"  \#[0-9a-f]{3,8}"
    r"| rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)"
    r"| rgba\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*(?:\d*\.)?\d+\s*\)"
    r"| hsl\(\s*\d{1,3}\s*,\s*\d{1,3}%\s*,\s*\d{1,3}%\s*\)"
    r"| hsla\(\s*\d{1,3}\s*,\s*\d{1,3}%\s*,\s*\d{1,3}%\s*,\s*(?:\d*\.)?\d+\s*\)"
    r"| [a-z]{3,30}"
    r")$"
)


def extract_align(style: str) -> str:
    """Return a normalised ``text-align`` value from ``style`` (or "")."""

    if not style:
        return ""
    match = _ALIGN_RE.search(style)
    if not match:
        return ""
    value = match.group(1).strip().lower()
    return value if value in SAFE_ALIGN_VALUES else ""


def extract_color(style: str) -> str:
    """Return the ``color`` value from ``style`` if it passes the allow-list,
    otherwise ``""``.  Used to filter ``<span style>`` wrappers produced by
    Confluence so crafted input cannot smuggle arbitrary CSS into output.
    """

    if not style:
        return ""
    match = _COLOR_RE.search(style)
    if not match:
        return ""
    color = match.group(1).strip()
    return color if SAFE_COLOR_RE.match(color) else ""


def extract_background_color(style: str) -> str:
    """Return the ``background-color`` value from ``style`` if it passes the
    allow-list, otherwise ``""``.  See :func:`extract_color`.
    """

    if not style:
        return ""
    match = _BG_COLOR_RE.search(style)
    if not match:
        return ""
    color = match.group(1).strip()
    return color if SAFE_COLOR_RE.match(color) else ""


def build_span_style(style: str) -> str:
    """Compose a sanitised ``style`` value with only safe colour declarations.

    Returns ``""`` when nothing safe remains, letting callers drop the
    wrapper entirely.
    """

    parts: List[str] = []
    color = extract_color(style)
    if color:
        parts.append(f"color: {color}")
    bg = extract_background_color(style)
    if bg:
        parts.append(f"background-color: {bg}")
    return "; ".join(parts)
