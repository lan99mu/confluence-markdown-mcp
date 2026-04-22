"""High-level *service* layer used by both the CLI and the MCP server.

The service methods do the orchestration (fetch → convert → save, load →
convert → update) so that the transport-specific front-ends stay thin.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .client import ConfluenceClient
from .config import Settings, load_settings
from .converter import markdown_to_storage, storage_to_markdown
from .files import dump_markdown_file, load_markdown_file


@dataclass
class PullResult:
    page_id: str
    title: str
    space_key: str
    version: int
    markdown: str
    path: Optional[str] = None


@dataclass
class PushResult:
    page_id: str
    title: str
    version: int


class ConfluenceService:
    """Facade that combines :class:`ConfluenceClient` with local files."""

    def __init__(
        self,
        client: Optional[ConfluenceClient] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        if client is None:
            settings = settings or load_settings()
            client = ConfluenceClient.from_settings(settings)
        self.client = client
        self.settings = settings

    # -------------------------------------------------------------- pull
    def pull_page(
        self,
        page_id: str,
        output_path: Optional[str] = None,
    ) -> PullResult:
        """Fetch a wiki page and optionally persist it as a Markdown file."""

        page = self.client.get_page(str(page_id))
        storage = page.get("body", {}).get("storage", {}).get("value", "")
        markdown = storage_to_markdown(storage)

        resolved_path: Optional[str] = None
        if output_path:
            resolved_path = _resolve_output_path(
                output_path,
                self.settings,
                title=str(page.get("title", "")),
            )
            dump_markdown_file(resolved_path, page, markdown)

        return PullResult(
            page_id=str(page.get("id", page_id)),
            title=str(page.get("title", "")),
            space_key=str(page.get("space", {}).get("key", "")),
            version=int(page.get("version", {}).get("number", 1) or 1),
            markdown=markdown,
            path=resolved_path,
        )

    # -------------------------------------------------------------- push
    def push_page(
        self,
        file_path: str,
        page_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> PushResult:
        """Upload ``file_path`` back to Confluence.

        ``page_id`` is required – either explicitly or via the file's front
        matter.  ``title`` defaults to the file's front matter, falling back
        to the current page title.
        """

        metadata, body = load_markdown_file(file_path)
        target_id = str(page_id or metadata.get("page_id") or "").strip()
        if not target_id:
            raise ValueError(
                "page_id is required: pass it explicitly or include it in the "
                "file's front matter."
            )

        current = self.client.get_page(target_id)
        current_version = int(current.get("version", {}).get("number", 1) or 1)
        resolved_title = (
            title
            or metadata.get("title")
            or current.get("title")
            or ""
        )
        storage = markdown_to_storage(body)
        updated = self.client.update_page(
            target_id,
            resolved_title,
            storage,
            current_version + 1,
        )
        new_version = int(
            updated.get("version", {}).get("number", current_version + 1) or 0
        )
        return PushResult(
            page_id=target_id,
            title=resolved_title,
            version=new_version,
        )


def _resolve_output_path(
    path: str,
    settings: Optional[Settings],
    title: str = "",
) -> str:
    """Return an absolute path, honouring ``CONFLUENCE_MARKDOWN_DIR``.

    When ``path`` is relative *and* the user has configured a default
    workspace directory, files are written beneath it; otherwise the
    current working directory is used.

    When ``path`` refers to a directory (existing, or ending in a path
    separator), the page ``title`` is used as the filename – mirroring the
    behaviour of many wiki exporters and avoiding the need for the caller
    to repeat the title on the command line.
    """

    ends_with_sep = path.endswith(("/", os.sep))
    if os.path.isabs(path):
        absolute = path
    else:
        root = settings.markdown_dir if settings and settings.markdown_dir else None
        absolute = os.path.abspath(os.path.join(root, path) if root else path)

    is_dir = os.path.isdir(absolute) or ends_with_sep
    if is_dir:
        filename = _title_to_filename(title) + ".md"
        absolute = os.path.join(absolute, filename)
    return absolute


# Characters that are unsafe or awkward on common filesystems.  We keep the
# Unicode letters/numbers that make CJK titles readable and only strip what
# would actually cause problems.
_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _title_to_filename(title: str, fallback: str = "page") -> str:
    """Return a filesystem-safe file name derived from a page title."""

    cleaned = _UNSAFE_FILENAME_RE.sub(" ", title or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Strip leading dots to avoid accidentally producing hidden files.
    cleaned = cleaned.lstrip(".")
    if not cleaned:
        cleaned = fallback
    # Cap the length at a sensible value – some filesystems reject names
    # longer than 255 bytes and titles are often quite long in Chinese.
    max_length = 120
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip()
    return cleaned


def page_summary(result: Any) -> Dict[str, Any]:
    """Return a JSON-safe dict for the MCP layer."""

    if isinstance(result, PullResult):
        return {
            "page_id": result.page_id,
            "title": result.title,
            "space_key": result.space_key,
            "version": result.version,
            "path": result.path,
            "markdown_preview": result.markdown[:400],
        }
    if isinstance(result, PushResult):
        return {
            "page_id": result.page_id,
            "title": result.title,
            "version": result.version,
        }
    raise TypeError(f"unsupported result type {type(result).__name__}")
