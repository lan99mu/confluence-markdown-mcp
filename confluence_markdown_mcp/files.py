"""Read/write Markdown files with a YAML-ish front-matter header."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Tuple


FRONT_MATTER_DELIM = "---"


def dump_markdown_file(path: str, page: Dict[str, Any], markdown_body: str) -> None:
    """Write ``markdown_body`` to ``path`` together with a metadata header.

    The header looks like:

    .. code-block:: yaml

        ---
        page_id: "123456"
        title: "Hello world"
        space_key: "DOC"
        version: 3
        ---
    """

    page_id = str(page.get("id", ""))
    title = str(page.get("title", ""))
    space_key = str(page.get("space", {}).get("key", ""))
    version = int(page.get("version", {}).get("number", 1) or 1)

    front_matter = (
        f"{FRONT_MATTER_DELIM}\n"
        f"page_id: {json.dumps(page_id)}\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        f"space_key: {json.dumps(space_key)}\n"
        f"version: {version}\n"
        f"{FRONT_MATTER_DELIM}\n\n"
    )

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(front_matter)
        fh.write(markdown_body.rstrip() + "\n")


def load_markdown_file(path: str) -> Tuple[Dict[str, str], str]:
    """Return ``(front_matter_dict, body_text)`` from a markdown file."""

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()

    metadata: Dict[str, str] = {}
    body = text
    if text.startswith(FRONT_MATTER_DELIM + "\n"):
        end = text.find("\n" + FRONT_MATTER_DELIM + "\n", len(FRONT_MATTER_DELIM) + 1)
        if end != -1:
            header = text[len(FRONT_MATTER_DELIM) + 1 : end]
            body = text[end + len(FRONT_MATTER_DELIM) + 2 :]
            for line in header.splitlines():
                if ":" not in line:
                    continue
                key, raw_value = line.split(":", 1)
                key = key.strip()
                value = raw_value.strip()
                if value.startswith('"') and value.endswith('"'):
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        value = value[1:-1]
                metadata[key] = str(value)

    return metadata, body.lstrip("\n")
