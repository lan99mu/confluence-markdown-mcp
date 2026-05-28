"""MCP server exposing Confluence ↔ Markdown tools.

Built on top of the official `mcp` Python SDK (``mcp.server.fastmcp``),
which implements the latest Model Context Protocol stdio / SSE /
streamable-http transports.

To keep page bodies out of the LLM context window (and to make the tools
deployable in remote / containerised environments where the server has
no access to the caller's filesystem), this server exchanges Markdown
and attachments as **MCP file streams**:

* ``pull_page`` returns the Markdown body — and any referenced
  attachments — as :class:`mcp.types.EmbeddedResource` blobs with a
  small JSON metadata header. MCP hosts surface these as downloadable
  files rather than feeding them straight into the model context.
* ``push_page`` accepts the Markdown body (and any attachments) as
  base64-encoded file streams supplied by the caller. The server never
  reads or writes the caller's local filesystem.
* ``read_page`` is the read-only equivalent of ``pull_page`` (no
  attachment uploads).
* Resource ``confluence://page/{page_id}`` is the host-fetched
  read-only Markdown view of a page, useful for clients that prefer
  resources over tools.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - import guard for users without the SDK
    from mcp.server.fastmcp import FastMCP
    from mcp.types import (
        BlobResourceContents,
        EmbeddedResource,
        TextContent,
    )
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "The 'mcp' package is required to run the MCP server. "
        "Install it with `pip install mcp` (or `pip install .[mcp]`)."
    ) from exc

from .converter.macros import sanitize_attachment_filename
from .service import ConfluenceService, PullResult


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- helpers


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _pull_metadata(result: PullResult, markdown_uri: str) -> Dict[str, Any]:
    """Return the small JSON header that accompanies the file-stream blobs.

    The page body itself is intentionally *not* included here so it is
    not forced into the model's context window.
    """

    return {
        "page_id": result.page_id,
        "title": result.title,
        "space_key": result.space_key,
        "version": result.version,
        "markdown_resource_uri": markdown_uri,
        "markdown_bytes": len(result.markdown.encode("utf-8")),
        "attachments": [
            {
                "filename": a.filename,
                "media_type": a.media_type,
                "size": a.size,
                "action": a.action,
                "attachment_id": a.attachment_id,
                "resource_uri": (
                    f"confluence://page/{result.page_id}/attachment/{a.filename}"
                    if a.action in {"downloaded", "skipped"}
                    else None
                ),
            }
            for a in result.attachments
        ],
    }


def _pull_to_content_blocks(
    service: ConfluenceService,
    page_id: str,
    include_attachments: bool,
) -> List[Any]:
    """Run a pull and return ``[text-metadata, markdown-blob, *attachment-blobs]``.

    Attachments are streamed through a private temporary directory so the
    existing service-layer logic can be reused unchanged; the directory
    is removed before this function returns.
    """

    workdir = tempfile.mkdtemp(prefix="confluence-mcp-pull-")
    try:
        result = service.pull_page(
            page_id=page_id,
            output_path=workdir + os.sep,
            download_attachments=include_attachments,
            attachments_dir="attachments",
        )

        markdown_uri = f"confluence://page/{result.page_id}.md"
        metadata = _pull_metadata(result, markdown_uri)

        blocks: List[Any] = [
            TextContent(
                type="text",
                text=json.dumps(metadata, ensure_ascii=False, indent=2),
            ),
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    uri=markdown_uri,
                    mimeType="text/markdown",
                    blob=_b64(result.markdown.encode("utf-8")),
                ),
            ),
        ]

        for att in result.attachments:
            if not att.path or not os.path.isfile(att.path):
                continue
            try:
                with open(att.path, "rb") as fh:
                    data = fh.read()
            except OSError as exc:  # pragma: no cover - I/O edge case
                logger.warning("could not read attachment %s: %s", att.path, exc)
                continue
            blocks.append(
                EmbeddedResource(
                    type="resource",
                    resource=BlobResourceContents(
                        uri=(
                            f"confluence://page/{result.page_id}"
                            f"/attachment/{att.filename}"
                        ),
                        mimeType=att.media_type or "application/octet-stream",
                        blob=_b64(data),
                    ),
                )
            )

        return blocks
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _decode_attachment_payload(item: Dict[str, Any]) -> tuple[str, bytes]:
    """Validate one entry from the ``attachments`` push parameter."""

    if not isinstance(item, dict):
        raise ValueError("each attachment must be an object with filename + content_base64")
    raw_name = str(item.get("filename") or "").strip()
    if not raw_name:
        raise ValueError("attachment.filename is required")
    if "/" in raw_name or "\\" in raw_name or raw_name in {".", ".."} \
            or raw_name.startswith(".."):
        raise ValueError(f"unsafe attachment filename: {raw_name!r}")
    safe_name = sanitize_attachment_filename(raw_name)
    if not safe_name or safe_name in {".", ".."} or "/" in safe_name or "\\" in safe_name:
        raise ValueError(f"unsafe attachment filename: {raw_name!r}")

    payload = item.get("content_base64")
    if not isinstance(payload, str) or not payload:
        raise ValueError(f"attachment {safe_name!r} is missing content_base64")
    try:
        data = base64.b64decode(payload, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError(f"attachment {safe_name!r} has invalid base64: {exc}") from exc
    return safe_name, data


def _push_from_streams(
    service: ConfluenceService,
    markdown_base64: str,
    page_id: Optional[str],
    title: Optional[str],
    upload_attachments: bool,
    attachments: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Materialise the caller-supplied streams in a temp dir and push.

    The temp directory is created and destroyed inside the call so no
    caller-supplied bytes ever persist on the server's filesystem.
    """

    if not isinstance(markdown_base64, str) or not markdown_base64:
        raise ValueError("markdown_base64 is required")
    try:
        markdown_bytes = base64.b64decode(markdown_base64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError(f"markdown_base64 is not valid base64: {exc}") from exc

    workdir = tempfile.mkdtemp(prefix="confluence-mcp-push-")
    try:
        md_path = os.path.join(workdir, "page.md")
        with open(md_path, "wb") as fh:
            fh.write(markdown_bytes)

        if attachments:
            att_dir = os.path.join(workdir, "attachments")
            os.makedirs(att_dir, exist_ok=True)
            for item in attachments:
                safe_name, data = _decode_attachment_payload(item)
                with open(os.path.join(att_dir, safe_name), "wb") as fh:
                    fh.write(data)

        result = service.push_page(
            file_path=md_path,
            page_id=page_id,
            title=title,
            upload_attachments=upload_attachments,
        )
        return {
            "page_id": result.page_id,
            "title": result.title,
            "version": result.version,
            "attachments": [
                {
                    "filename": a.filename,
                    "media_type": a.media_type,
                    "size": a.size,
                    "action": a.action,
                    "attachment_id": a.attachment_id,
                }
                for a in result.attachments
            ],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------- factory


def create_server(
    service: Optional[ConfluenceService] = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    mount_path: str = "/",
    sse_path: str = "/sse",
    streamable_http_path: str = "/mcp",
    json_response: bool = False,
    stateless_http: bool = False,
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
            "Pull Confluence wiki pages as Markdown file streams and push "
            "Markdown file streams back. Configure CONFLUENCE_BASE_URL "
            "plus either CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN, or "
            "CONFLUENCE_PAT, in the environment. pull_page returns the "
            "Markdown body and attachments as MCP EmbeddedResource blobs; "
            "push_page expects the Markdown body (and any attachments) "
            "as base64 file streams supplied by the caller."
        ),
        host=host,
        port=port,
        mount_path=mount_path,
        sse_path=sse_path,
        streamable_http_path=streamable_http_path,
        json_response=json_response,
        stateless_http=stateless_http,
    )

    _state: Dict[str, Any] = {"service": service}

    def _service() -> ConfluenceService:
        if _state["service"] is None:
            _state["service"] = ConfluenceService()
        return _state["service"]

    @app.tool(
        name="pull_page",
        description=(
            "Download a Confluence page and return it as an MCP file "
            "stream. The response is a short JSON metadata block "
            "(page_id, title, space_key, version, attachment list) "
            "followed by EmbeddedResource blobs containing the Markdown "
            "body (mimeType text/markdown) and, when "
            "download_attachments is true, each referenced attachment "
            "(images / files) as its own blob. The raw Markdown text is "
            "NOT returned inline, so it does not get fed into the model's "
            "context window — the host client is expected to persist the "
            "blobs as files. There is no server-side output path: this "
            "tool is safe to run on a remote / containerised MCP server."
        ),
    )
    def pull_page(
        page_id: str,
        download_attachments: bool = True,
    ) -> List[Any]:
        return _pull_to_content_blocks(
            _service(),
            page_id=page_id,
            include_attachments=download_attachments,
        )

    @app.tool(
        name="push_page",
        description=(
            "Upload a Markdown file stream back to a Confluence page. "
            "The Markdown body must be supplied as base64 in "
            "markdown_base64 (the caller — typically the MCP host — is "
            "responsible for reading the local file and encoding it). "
            "page_id is taken from the explicit argument or from the "
            "decoded file's YAML-style front matter. Additional local "
            "files referenced by the Markdown can be supplied as "
            "attachments=[{filename, content_base64}, ...] and will be "
            "created / updated as Confluence attachments before the page "
            "body is replaced. The server never touches the caller's "
            "local filesystem."
        ),
    )
    def push_page(
        markdown_base64: str,
        page_id: Optional[str] = None,
        title: Optional[str] = None,
        upload_attachments: bool = True,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return _push_from_streams(
            _service(),
            markdown_base64=markdown_base64,
            page_id=page_id,
            title=title,
            upload_attachments=upload_attachments,
            attachments=attachments,
        )

    @app.tool(
        name="read_page",
        description=(
            "Fetch a Confluence page and return its Markdown body as a "
            "single EmbeddedResource blob (mimeType text/markdown). "
            "Identical to pull_page but never downloads attachments and "
            "never returns the body inline as text."
        ),
    )
    def read_page(page_id: str) -> List[Any]:
        return _pull_to_content_blocks(
            _service(),
            page_id=page_id,
            include_attachments=False,
        )

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
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    mount_path: str = "/",
    sse_path: str = "/sse",
    streamable_http_path: str = "/mcp",
    json_response: bool = False,
    stateless_http: bool = False,
) -> None:
    """Entry point used by the ``confluence-markdown-mcp serve`` command.

    Parameters
    ----------
    transport:
        MCP transport to use. One of ``stdio`` (default), ``sse`` or
        ``streamable-http``. The two HTTP transports expose the server over
        a real network socket so that the same MCP can be reused by remote
        clients or run inside a container.
    host / port:
        Bind address for the HTTP transports. Ignored when ``transport`` is
        ``stdio``. Default ``127.0.0.1:8000``; bind to ``0.0.0.0`` when
        running inside a container.
    mount_path / sse_path / streamable_http_path:
        URL paths the corresponding HTTP transport is mounted on.
    json_response / stateless_http:
        Forwarded to ``FastMCP``. ``stateless_http=True`` is convenient for
        load-balanced container deployments where each request may hit a
        different replica.
    """

    if transport not in {"stdio", "sse", "streamable-http"}:
        raise ValueError(
            "Unsupported transport: "
            f"{transport!r}. Expected one of 'stdio', 'sse', 'streamable-http'."
        )

    server = create_server(
        host=host,
        port=port,
        mount_path=mount_path,
        sse_path=sse_path,
        streamable_http_path=streamable_http_path,
        json_response=json_response,
        stateless_http=stateless_http,
    )
    server.run(transport=transport)


def run(
    transport: str = "stdio",
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    mount_path: str = "/",
    sse_path: str = "/sse",
    streamable_http_path: str = "/mcp",
    json_response: bool = False,
    stateless_http: bool = False,
) -> None:
    """Entry point used by the ``confluence-markdown-mcp serve`` command.

    Parameters
    ----------
    transport:
        MCP transport to use. One of ``stdio`` (default), ``sse`` or
        ``streamable-http``. The two HTTP transports expose the server over
        a real network socket so that the same MCP can be reused by remote
        clients or run inside a container.
    host / port:
        Bind address for the HTTP transports. Ignored when ``transport`` is
        ``stdio``. Default ``127.0.0.1:8000``; bind to ``0.0.0.0`` when
        running inside a container.
    mount_path / sse_path / streamable_http_path:
        URL paths the corresponding HTTP transport is mounted on.
    json_response / stateless_http:
        Forwarded to ``FastMCP``. ``stateless_http=True`` is convenient for
        load-balanced container deployments where each request may hit a
        different replica.
    """

    if transport not in {"stdio", "sse", "streamable-http"}:
        raise ValueError(
            "Unsupported transport: "
            f"{transport!r}. Expected one of 'stdio', 'sse', 'streamable-http'."
        )

    server = create_server(
        host=host,
        port=port,
        mount_path=mount_path,
        sse_path=sse_path,
        streamable_http_path=streamable_http_path,
        json_response=json_response,
        stateless_http=stateless_http,
    )
    server.run(transport=transport)
