"""MCP server exposing Confluence ↔ Markdown tools.

Built on top of the official `mcp` Python SDK (``mcp.server.fastmcp``)
and exposed only through the local stdio transport.

Tools use local file paths so the MCP host can persist Markdown on disk:

* ``pull_page`` fetches a Confluence page as Markdown.  When
  ``output_dir`` is provided the file is saved there (filename
  auto-generated from the page title); otherwise the content is
  returned directly.
* ``push_page`` reads a local ``.md`` file and uploads it to
  Confluence.
* ``read_page`` returns the Markdown body without writing to disk.
* Resource ``confluence://page/{page_id}`` is the host-fetched
  read-only Markdown view of a page, useful for clients that prefer
  resources over tools.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

try:  # pragma: no cover - import guard for users without the SDK
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "The 'mcp' package is required to run the MCP server. "
        "Install it with `pip install mcp` (or `pip install .[mcp]`)."
    ) from exc

from .service import ConfluenceService


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- factory


def create_server(
    service: Optional[ConfluenceService] = None,
) -> FastMCP:
    """Build and return a configured :class:`FastMCP` instance.

    The service is created lazily on the first tool invocation so that the
    server can start even when credentials are only injected later (for
    example by MCP clients that set environment variables just before
    spawning the child process).
    """

    app = FastMCP(
        name="confluence-markdown-mcp",
        instructions=(
            "Pull Confluence wiki pages as Markdown and push local "
            "Markdown files back. Configure CONFLUENCE_BASE_URL plus "
            "either CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN, or "
            "CONFLUENCE_PAT, in the environment. pull_page can save to "
            "a local directory or return content directly; push_page "
            "reads a local .md file path."
        ),
    )

    _state: Dict[str, Any] = {"service": service}

    def _service() -> ConfluenceService:
        if _state["service"] is None:
            _state["service"] = ConfluenceService()
        return _state["service"]

    @app.tool(
        name="pull_page",
        description=(
            "Download a Confluence page as Markdown. When output_dir is "
            "provided, the Markdown file is saved to that directory "
            "(filename is auto-generated from the page title) and the "
            "result includes the file path. When output_dir is omitted, "
            "the Markdown content is returned directly in the response."
        ),
    )
    def pull_page(
        page_id: str,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        if output_dir:
            # Ensure the path ends with a separator so the service layer
            # treats it as a directory and auto-generates the filename.
            dir_path = output_dir.rstrip("/\\") + os.sep
            os.makedirs(dir_path, exist_ok=True)
            result = _service().pull_page(
                page_id=page_id,
                output_path=dir_path,
                download_attachments=True,
            )
            return {
                "page_id": result.page_id,
                "title": result.title,
                "space_key": result.space_key,
                "version": result.version,
                "file_path": result.path,
            }
        else:
            result = _service().pull_page(page_id=page_id, output_path=None)
            return {
                "page_id": result.page_id,
                "title": result.title,
                "space_key": result.space_key,
                "version": result.version,
                "content": result.markdown,
            }

    @app.tool(
        name="push_page",
        description=(
            "Upload a local Markdown file to a Confluence page. "
            "file_path is the path to the .md file on disk. "
            "page_id can be passed explicitly or read from the file's "
            "YAML front matter. title defaults to the front matter "
            "title or the current page title on Confluence."
        ),
    )
    def push_page(
        file_path: str,
        page_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not file_path or not os.path.isfile(file_path):
            raise ValueError(f"file_path is not a valid file: {file_path!r}")
        result = _service().push_page(
            file_path=file_path,
            page_id=page_id,
            title=title,
            upload_attachments=True,
        )
        return {
            "page_id": result.page_id,
            "title": result.title,
            "version": result.version,
        }

    @app.tool(
        name="read_page",
        description=(
            "Fetch a Confluence page and return its Markdown content "
            "directly (no file is written to disk)."
        ),
    )
    def read_page(page_id: str) -> Dict[str, Any]:
        result = _service().pull_page(page_id=page_id, output_path=None)
        return {
            "page_id": result.page_id,
            "title": result.title,
            "space_key": result.space_key,
            "version": result.version,
            "content": result.markdown,
        }

    @app.resource("confluence://page/{page_id}")
    def page_resource(page_id: str) -> str:
        # Resources are explicitly fetched by the host, so returning the
        # Markdown text here is by design (and matches the MCP resource
        # contract). The model still does not see it unless the host
        # decides to attach it.
        result = _service().pull_page(page_id=page_id, output_path=None)
        return result.markdown

    return app


def run(
    transport: str = "stdio",
) -> None:
    """Entry point used by the ``confluence-markdown-mcp serve`` command.

    Only stdio is supported for the MCP server. Use ``serve-http`` for the
    separate non-MCP FastAPI service.
    """

    if transport != "stdio":
        raise ValueError(
            "Unsupported transport: "
            f"{transport!r}. MCP HTTP transports are disabled; expected 'stdio'."
        )

    server = create_server()
    server.run(transport=transport)
