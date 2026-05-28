"""FastAPI HTTP API exposing pull / push operations over plain HTTP.

This is a thin alternative to the MCP server in :mod:`server`. While MCP
is ideal for local desktop agents (Claude Desktop, Cursor, …), a plain
HTTP API is more convenient for remote / cloud deployments and for
clients that want to upload files directly as ``multipart/form-data``
instead of base64-encoded JSON payloads.

Both layers share the same underlying :class:`ConfluenceService`, so the
business logic (Markdown ⇄ storage conversion, attachment handling,
etc.) is identical regardless of transport.

Endpoints
---------

* ``GET  /healthz``
    Liveness probe.

* ``GET  /pages/{page_id}``
    Return page metadata plus the rendered Markdown body and an
    attachment manifest as JSON. Query parameter
    ``download_attachments`` (default ``true``) controls whether
    attachment metadata is populated.

* ``GET  /pages/{page_id}/markdown``
    Return the Markdown body as ``text/markdown``.

* ``GET  /pages/{page_id}/attachments/{filename}``
    Stream an individual attachment back to the caller.

* ``POST /pages/{page_id}``
    Replace the page body. Expects ``multipart/form-data`` with:

    * ``md_file`` – the Markdown file (required)
    * ``attachments`` – zero or more attachment files (optional, may be
      repeated; only files actually referenced by the Markdown will be
      uploaded to Confluence)
    * ``title`` – optional page title override (form field)
    * ``upload_attachments`` – optional boolean (form field, default
      ``true``)
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - import guard for environments without FastAPI
    from fastapi import (
        Depends,
        FastAPI,
        File,
        Form,
        HTTPException,
        Query,
        UploadFile,
    )
    from fastapi.responses import FileResponse, JSONResponse, Response
    from starlette.background import BackgroundTask
except ImportError as exc:  # pragma: no cover - re-raised at call time
    raise RuntimeError(
        "The 'fastapi' package is required to use the HTTP API. "
        "Install it with `pip install .[http]`."
    ) from exc

from .client import ConfluenceError
from .converter.macros import sanitize_attachment_filename
from .service import AttachmentInfo, ConfluenceService, PullResult, PushResult


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- helpers


def _parse_bool_form(raw: Optional[str], default: bool) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if not value:
        return default
    if value in {"true", "1", "yes", "y", "on"}:
        return True
    if value in {"false", "0", "no", "n", "off"}:
        return False
    raise HTTPException(status_code=400, detail=f"invalid boolean value: {raw!r}")


def _safe_attachment_filename(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="attachment filename is required")
    if "/" in raw or "\\" in raw or raw in {".", ".."} or raw.startswith(".."):
        raise HTTPException(status_code=400, detail=f"unsafe attachment filename: {raw!r}")
    safe = sanitize_attachment_filename(raw)
    if not safe or safe in {".", ".."} or "/" in safe or "\\" in safe:
        raise HTTPException(status_code=400, detail=f"unsafe attachment filename: {raw!r}")
    return safe


def _attachment_summary(info: AttachmentInfo, page_id: str) -> Dict[str, Any]:
    return {
        "filename": info.filename,
        "media_type": info.media_type,
        "size": info.size,
        "action": info.action,
        "attachment_id": info.attachment_id,
        "download_url": (
            f"/pages/{page_id}/attachments/{info.filename}"
            if info.action in {"downloaded", "skipped"}
            else None
        ),
    }


def _pull_to_payload(result: PullResult) -> Dict[str, Any]:
    return {
        "page_id": result.page_id,
        "title": result.title,
        "space_key": result.space_key,
        "version": result.version,
        "markdown": result.markdown,
        "attachments": [
            _attachment_summary(a, result.page_id) for a in result.attachments
        ],
    }


def _push_to_payload(result: PushResult) -> Dict[str, Any]:
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


# ---------------------------------------------------------------- factory


def create_app(service: Optional[ConfluenceService] = None) -> FastAPI:
    """Build and return a configured :class:`FastAPI` app.

    The :class:`ConfluenceService` is created lazily on the first request
    so the app can start even when credentials are only injected later.
    """

    app = FastAPI(
        title="confluence-markdown-http",
        version="0.2.0",
        description=(
            "HTTP front-end for confluence-markdown-mcp. Pull Confluence "
            "pages as Markdown and push Markdown (with attachments) back "
            "via plain multipart/form-data uploads — no MCP host required."
        ),
    )

    _state: Dict[str, Any] = {"service": service}

    def get_service() -> ConfluenceService:
        if _state["service"] is None:
            _state["service"] = ConfluenceService()
        return _state["service"]

    # -------------------------------------------------------------- health

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        return {"ok": True}

    # ---------------------------------------------------------------- pull

    @app.get("/pages/{page_id}")
    def get_page(
        page_id: str,
        download_attachments: bool = Query(
            default=True,
            description=(
                "When true, populate the attachment manifest with metadata "
                "downloaded from Confluence; clients can fetch each "
                "attachment's bytes via /pages/{page_id}/attachments/{name}."
            ),
        ),
        svc: ConfluenceService = Depends(get_service),
    ) -> JSONResponse:
        workdir = tempfile.mkdtemp(prefix="confluence-http-pull-")
        try:
            result = svc.pull_page(
                page_id=page_id,
                output_path=workdir + os.sep if download_attachments else None,
                download_attachments=download_attachments,
                attachments_dir="attachments",
            )
            return JSONResponse(_pull_to_payload(result))
        except ConfluenceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @app.get("/pages/{page_id}/markdown")
    def get_page_markdown(
        page_id: str,
        svc: ConfluenceService = Depends(get_service),
    ) -> Response:
        try:
            result = svc.pull_page(page_id=page_id, output_path=None)
        except ConfluenceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(
            content=result.markdown,
            media_type="text/markdown; charset=utf-8",
        )

    @app.get("/pages/{page_id}/attachments/{filename}")
    def get_page_attachment(
        page_id: str,
        filename: str,
        svc: ConfluenceService = Depends(get_service),
    ) -> Response:
        safe_name = _safe_attachment_filename(filename)
        try:
            attachments = svc.client.list_attachments(page_id)
        except ConfluenceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        match: Optional[Dict[str, Any]] = None
        for item in attachments:
            title = str(item.get("title") or "")
            if title == safe_name or sanitize_attachment_filename(title) == safe_name:
                match = item
                break
        if match is None:
            raise HTTPException(
                status_code=404,
                detail=f"attachment {safe_name!r} not found on page {page_id}",
            )

        download_url = (
            (match.get("_links") or {}).get("download")
            or (match.get("_links") or {}).get("self")
        )
        if not download_url:
            raise HTTPException(
                status_code=502,
                detail=f"attachment {safe_name!r} has no download link",
            )

        workdir = tempfile.mkdtemp(prefix="confluence-http-att-")
        # Use a fixed on-disk filename so the path we open is provably
        # independent of any user-controlled input. The user-facing
        # download filename is still ``safe_name`` via Content-Disposition.
        dest = os.path.join(workdir, "attachment.bin")
        try:
            svc.client.download_attachment(download_url, dest)
        except ConfluenceError as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        media_type = (
            (match.get("extensions") or {}).get("mediaType")
            or (match.get("metadata") or {}).get("mediaType")
            or "application/octet-stream"
        )
        # Clean the temp dir up after the file has finished streaming.
        cleanup = BackgroundTask(shutil.rmtree, workdir, ignore_errors=True)
        return FileResponse(
            dest,
            media_type=media_type,
            filename=safe_name,
            background=cleanup,
        )

    # ---------------------------------------------------------------- push

    @app.post("/pages/{page_id}")
    async def post_page(
        page_id: str,
        md_file: UploadFile = File(
            ...,
            description="The Markdown file to upload (multipart/form-data field).",
        ),
        attachments: Optional[List[UploadFile]] = File(
            default=None,
            description=(
                "Optional attachment files referenced by the Markdown. "
                "Repeat the field to upload several files."
            ),
        ),
        title: Optional[str] = Form(
            default=None,
            description="Optional override for the page title.",
        ),
        upload_attachments: Optional[str] = Form(
            default=None,
            description="Set to 'false' to skip attachment uploads.",
        ),
        svc: ConfluenceService = Depends(get_service),
    ) -> JSONResponse:
        do_attachments = _parse_bool_form(upload_attachments, default=True)
        if not md_file or not md_file.filename:
            raise HTTPException(status_code=400, detail="md_file is required")

        try:
            markdown_bytes = await md_file.read()
        finally:
            await md_file.close()
        if not markdown_bytes:
            raise HTTPException(status_code=400, detail="md_file is empty")

        workdir = tempfile.mkdtemp(prefix="confluence-http-push-")
        try:
            md_path = os.path.join(workdir, "page.md")
            with open(md_path, "wb") as fh:
                fh.write(markdown_bytes)

            if attachments:
                att_dir = os.path.join(workdir, "attachments")
                os.makedirs(att_dir, exist_ok=True)
                for item in attachments:
                    if item is None or not item.filename:
                        continue
                    safe_name = _safe_attachment_filename(item.filename)
                    target = os.path.normpath(os.path.join(att_dir, safe_name))
                    if os.path.commonpath([target, att_dir]) != att_dir:
                        raise HTTPException(
                            status_code=400,
                            detail=f"unsafe attachment filename: {item.filename!r}",
                        )
                    try:
                        data = await item.read()
                    finally:
                        await item.close()
                    with open(target, "wb") as fh:
                        fh.write(data)

            try:
                result = svc.push_page(
                    file_path=md_path,
                    page_id=page_id,
                    title=title,
                    upload_attachments=do_attachments,
                )
            except ConfluenceError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            return JSONResponse(_push_to_payload(result))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    return app


# ---------------------------------------------------------------- runner


def run(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    log_level: str = "info",
) -> None:
    """Run the FastAPI app with uvicorn (used by ``serve-http`` CLI)."""

    try:  # pragma: no cover - optional dependency
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "uvicorn is required to run the HTTP server. "
            "Install it with `pip install .[http]`."
        ) from exc

    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level=log_level)
