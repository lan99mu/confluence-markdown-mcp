"""PlantUML iframe helpers.

The target wiki does not provide a native ``plantuml`` structured macro, but
it does render iframes wrapped in the ``html-bobswift`` macro.  PlantUML fenced
code blocks therefore become iframes that point at the public PlantUML SVG
endpoint, and generated URLs can be decoded back into fenced blocks on pull.
"""

from __future__ import annotations

import re
import zlib
from typing import Optional


PLANTUML_SERVER = "https://www.plantuml.com/plantuml"
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"
_DECODE = {char: idx for idx, char in enumerate(_ALPHABET)}
_BYTES_PER_CHUNK = 3
_CHARS_PER_CHUNK = 4
_PLANTUML_URL_RE = re.compile(
    r"^https://www\.plantuml\.com/plantuml/(?:svg|png|txt)/(?P<data>[0-9A-Za-z\-_]+)$"
)


def plantuml_iframe(markup: str) -> str:
    encoded = _encode_plantuml(markup)
    return (
        f'<iframe src="{PLANTUML_SERVER}/svg/{encoded}" '
        'width="100%" height="600" frameborder="0" '
        'title="PlantUML diagram"></iframe>'
    )


def decode_plantuml_url(src: str) -> Optional[str]:
    match = _PLANTUML_URL_RE.match((src or "").strip())
    if not match:
        return None
    try:
        compressed = _decode_plantuml(match.group("data"))
        return zlib.decompress(compressed, -15).decode("utf-8")
    except (KeyError, ValueError, zlib.error, UnicodeDecodeError):
        return None


def _encode_plantuml(markup: str) -> str:
    # PlantUML's URL format uses raw deflate, so strip zlib's 2-byte header
    # and trailing 4-byte ADLER32 checksum from Python's compressed payload.
    compressed = zlib.compress(markup.encode("utf-8"))[2:-4]
    chunks = []
    for i in range(0, len(compressed), _BYTES_PER_CHUNK):
        end = i + _BYTES_PER_CHUNK
        chunk = compressed[i:end]
        b1 = chunk[0]
        b2 = chunk[1] if len(chunk) > 1 else 0
        b3 = chunk[2] if len(chunk) > 2 else 0
        c1 = b1 >> 2
        c2 = ((b1 & 0x3) << 4) | (b2 >> 4)
        c3 = ((b2 & 0xF) << 2) | (b3 >> 6)
        c4 = b3 & 0x3F
        encoded = _ALPHABET[c1] + _ALPHABET[c2]
        if len(chunk) > 1:
            encoded += _ALPHABET[c3]
        if len(chunk) > 2:
            encoded += _ALPHABET[c4]
        chunks.append(encoded)
    return "".join(chunks)


def _decode_plantuml(encoded: str) -> bytes:
    out = bytearray()
    for i in range(0, len(encoded), _CHARS_PER_CHUNK):
        end = i + _CHARS_PER_CHUNK
        chunk = encoded[i:end]
        if len(chunk) < 2:
            raise ValueError("invalid PlantUML payload")
        c1 = _DECODE[chunk[0]]
        c2 = _DECODE[chunk[1]]
        c3 = _DECODE[chunk[2]] if len(chunk) > 2 else 0
        c4 = _DECODE[chunk[3]] if len(chunk) > 3 else 0
        out.append((c1 << 2) | (c2 >> 4))
        if len(chunk) > 2:
            out.append(((c2 & 0xF) << 4) | (c3 >> 2))
        if len(chunk) > 3:
            out.append(((c3 & 0x3) << 6) | c4)
    return bytes(out)
