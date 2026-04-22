"""High-level *service* layer used by both the CLI and the MCP server.

The service methods do the orchestration (fetch → convert → save, load →
convert → update) so that the transport-specific front-ends stay thin.
"""

from __future__ import annotations

import os
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
            resolved_path = _resolve_output_path(output_path, self.settings)
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


def _resolve_output_path(path: str, settings: Optional[Settings]) -> str:
    """Return an absolute path, honouring ``CONFLUENCE_MARKDOWN_DIR``.

    When ``path`` is relative *and* the user has configured a default
    workspace directory, files are written beneath it; otherwise the
    current working directory is used.
    """

    if os.path.isabs(path):
        return path
    root = settings.markdown_dir if settings and settings.markdown_dir else None
    return os.path.abspath(os.path.join(root, path) if root else path)


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
