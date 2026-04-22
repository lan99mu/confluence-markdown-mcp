"""High-level *service* layer used by both the CLI and the MCP server.

The service methods do the orchestration (fetch → convert → save, load →
convert → update) so that the transport-specific front-ends stay thin.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .client import ConfluenceClient, ConfluenceError, file_sha1
from .config import Settings, load_settings
from .converter import markdown_to_storage, storage_to_markdown
from .converter.macros import (
    ATTACHMENTS_DIRNAME,
    iter_referenced_filenames,
    sanitize_attachment_filename,
)
from .files import dump_markdown_file, load_markdown_file


logger = logging.getLogger(__name__)


@dataclass
class AttachmentInfo:
    """Metadata about an attachment handled during pull / push."""

    filename: str
    path: Optional[str] = None
    media_type: Optional[str] = None
    size: Optional[int] = None
    action: Optional[str] = None  # created / updated / skipped / downloaded
    attachment_id: Optional[str] = None


@dataclass
class PullResult:
    page_id: str
    title: str
    space_key: str
    version: int
    markdown: str
    path: Optional[str] = None
    attachments: List[AttachmentInfo] = field(default_factory=list)


@dataclass
class PushResult:
    page_id: str
    title: str
    version: int
    attachments: List[AttachmentInfo] = field(default_factory=list)


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
        download_attachments: bool = True,
        attachments_dir: str = ATTACHMENTS_DIRNAME,
    ) -> PullResult:
        """Fetch a wiki page and optionally persist it as a Markdown file.

        When ``output_path`` is provided and ``download_attachments`` is
        true, every ``ri:filename`` referenced in the page body is fetched
        into ``<md-dir>/<attachments_dir>/<filename>`` – so the resulting
        file renders natively in common Markdown viewers.
        """

        page = self.client.get_page(str(page_id))
        storage = page.get("body", {}).get("storage", {}).get("value", "")
        markdown = storage_to_markdown(storage)

        resolved_path: Optional[str] = None
        attachments: List[AttachmentInfo] = []
        if output_path:
            resolved_path = _resolve_output_path(
                output_path,
                self.settings,
                title=str(page.get("title", "")),
            )
            dump_markdown_file(resolved_path, page, markdown)

            if download_attachments:
                attachments = self._download_referenced_attachments(
                    page_id=str(page.get("id", page_id)),
                    storage=storage,
                    markdown_path=resolved_path,
                    attachments_dirname=attachments_dir or ATTACHMENTS_DIRNAME,
                )

        return PullResult(
            page_id=str(page.get("id", page_id)),
            title=str(page.get("title", "")),
            space_key=str(page.get("space", {}).get("key", "")),
            version=int(page.get("version", {}).get("number", 1) or 1),
            markdown=markdown,
            path=resolved_path,
            attachments=attachments,
        )

    # -------------------------------------------------------------- push
    def push_page(
        self,
        file_path: str,
        page_id: Optional[str] = None,
        title: Optional[str] = None,
        upload_attachments: bool = True,
    ) -> PushResult:
        """Upload ``file_path`` back to Confluence.

        ``page_id`` is required – either explicitly or via the file's front
        matter.  ``title`` defaults to the file's front matter, falling back
        to the current page title.  When ``upload_attachments`` is true,
        any local files referenced by the Markdown are created / updated
        as Confluence attachments *before* the page body is replaced.
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

        uploaded: List[AttachmentInfo] = []
        if upload_attachments:
            uploaded = self._upload_local_attachments(
                page_id=target_id,
                markdown_path=file_path,
                markdown_body=body,
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
            attachments=uploaded,
        )

    # ---------------------------------------------------------- attachments
    def _download_referenced_attachments(
        self,
        page_id: str,
        storage: str,
        markdown_path: str,
        attachments_dirname: str,
    ) -> List[AttachmentInfo]:
        """Download every ``ri:filename`` that the page body references.

        Returns one :class:`AttachmentInfo` per referenced file.  Files
        that are already present on disk and whose size/version matches
        the server metadata are skipped so repeat pulls are cheap.
        """

        referenced = list(iter_referenced_filenames(storage))
        if not referenced:
            return []

        remote = self._attachment_index(page_id)
        target_dir = os.path.join(os.path.dirname(os.path.abspath(markdown_path)),
                                  attachments_dirname)

        results: List[AttachmentInfo] = []
        seen: Set[str] = set()
        for raw in referenced:
            safe = sanitize_attachment_filename(raw)
            if not safe or safe in seen:
                continue
            seen.add(safe)

            meta = remote.get(raw) or remote.get(safe)
            info = AttachmentInfo(filename=safe)
            if not meta:
                info.action = "missing"
                results.append(info)
                logger.warning(
                    "attachment %r referenced by page %s but not found on server",
                    raw, page_id,
                )
                continue

            dest = _safe_join(target_dir, safe)
            if dest is None:
                # Path traversal attempt – refuse and move on.
                info.action = "rejected"
                results.append(info)
                continue

            media_type = (
                (meta.get("extensions") or {}).get("mediaType")
                or (meta.get("metadata") or {}).get("mediaType")
            )
            remote_size = (meta.get("extensions") or {}).get("fileSize")
            info.media_type = media_type
            info.attachment_id = meta.get("id")

            if os.path.exists(dest) and remote_size is not None:
                try:
                    if int(remote_size) == os.path.getsize(dest):
                        info.action = "skipped"
                        info.size = os.path.getsize(dest)
                        info.path = dest
                        results.append(info)
                        continue
                except (OSError, ValueError):
                    pass

            download_url = ((meta.get("_links") or {}).get("download")
                            or (meta.get("_links") or {}).get("self"))
            if not download_url:
                info.action = "missing"
                results.append(info)
                continue

            try:
                self.client.download_attachment(download_url, dest)
            except ConfluenceError as exc:  # pragma: no cover - network
                info.action = f"failed: {exc}"
                results.append(info)
                continue

            info.action = "downloaded"
            info.path = dest
            try:
                info.size = os.path.getsize(dest)
            except OSError:
                info.size = None
            results.append(info)

        return results

    def _upload_local_attachments(
        self,
        page_id: str,
        markdown_path: str,
        markdown_body: str,
    ) -> List[AttachmentInfo]:
        """Upload every local file referenced by the Markdown body.

        Relative references that cannot be resolved on disk are reported
        back to the caller but never raise.
        """

        references = _collect_local_references(markdown_body)
        if not references:
            return []

        md_dir = os.path.dirname(os.path.abspath(markdown_path))
        remote = self._attachment_index(page_id)
        results: List[AttachmentInfo] = []
        seen_targets: Set[str] = set()

        for rel in references:
            # Resolve against the Markdown file's directory and make sure
            # the target stays inside the workspace; refuse obvious path
            # traversal attempts.
            absolute = os.path.normpath(os.path.join(md_dir, rel))
            try:
                if os.path.commonpath([absolute, md_dir]) != md_dir:
                    logger.warning(
                        "refusing to upload attachment outside markdown directory: %s",
                        rel,
                    )
                    continue
            except ValueError:
                # commonpath raises on mixed drives etc. – skip such refs.
                continue
            if absolute in seen_targets:
                continue
            seen_targets.add(absolute)
            if not os.path.isfile(absolute):
                results.append(AttachmentInfo(
                    filename=sanitize_attachment_filename(os.path.basename(rel)),
                    action="missing",
                ))
                continue

            filename = sanitize_attachment_filename(os.path.basename(absolute))
            info = AttachmentInfo(filename=filename, path=absolute)
            try:
                info.size = os.path.getsize(absolute)
            except OSError:
                info.size = None

            existing = remote.get(filename) or remote.get(os.path.basename(rel))
            try:
                local_hash = file_sha1(absolute)
            except OSError:
                local_hash = ""

            if existing is not None:
                remote_size = (existing.get("extensions") or {}).get("fileSize")
                if (
                    remote_size is not None
                    and info.size is not None
                    and int(remote_size) == info.size
                ):
                    info.action = "skipped"
                    info.attachment_id = existing.get("id")
                    results.append(info)
                    continue
                try:
                    response = self.client.update_attachment_data(
                        page_id,
                        str(existing.get("id")),
                        absolute,
                        comment=f"content-sha1:{local_hash}" if local_hash else None,
                    )
                    info.action = "updated"
                    info.attachment_id = str(
                        response.get("id") or existing.get("id") or ""
                    )
                except ConfluenceError as exc:
                    info.action = f"failed: {exc}"
            else:
                try:
                    response = self.client.create_attachment(
                        page_id,
                        absolute,
                        comment=f"content-sha1:{local_hash}" if local_hash else None,
                    )
                    # /child/attachment responds with {"results": [...]}.
                    first = (response.get("results") or [response])[0] if isinstance(
                        response, dict
                    ) else {}
                    info.action = "created"
                    info.attachment_id = str(first.get("id") or "")
                except ConfluenceError as exc:
                    info.action = f"failed: {exc}"

            results.append(info)

        return results

    def _attachment_index(self, page_id: str) -> Dict[str, Dict[str, Any]]:
        """Build a ``{filename: attachment_dict}`` map for ``page_id``.

        Both the literal and the sanitised filename are indexed so lookups
        succeed whether or not the remote name was previously scrubbed.
        """

        try:
            raw = self.client.list_attachments(page_id)
        except ConfluenceError:  # pragma: no cover - network failures
            return {}
        index: Dict[str, Dict[str, Any]] = {}
        for item in raw:
            title = str(item.get("title") or "")
            if title:
                index.setdefault(title, item)
                safe = sanitize_attachment_filename(title)
                if safe:
                    index.setdefault(safe, item)
        return index


# ---------------------------------------------------------------------------
# Local reference collection
# ---------------------------------------------------------------------------


# Very small subset of Markdown image / link syntax – we intentionally
# avoid running the full parser here because we only need relative paths
# to check them against the filesystem.  External URLs (``http(s)://``,
# ``data:``, ``mailto:``, protocol-relative ``//…``, or in-page anchors
# starting with ``#``) are filtered out.
_IMG_LINK_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)",
)
_LINK_RE = re.compile(
    r"(?<!\!)\[(?P<label>[^\]]*)\]\((?P<href>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
    r"(?P<marker>\s*<!--\s*cm-attachment\s*-->)?",
)


def _collect_local_references(markdown: str) -> List[str]:
    """Return relative paths referenced by images or marked attachments."""

    seen: Set[str] = set()
    ordered: List[str] = []
    for m in _IMG_LINK_RE.finditer(markdown or ""):
        src = m.group("src").strip()
        if _is_local_ref(src) and src not in seen:
            seen.add(src)
            ordered.append(src)
    for m in _LINK_RE.finditer(markdown or ""):
        if not m.group("marker"):
            continue
        href = m.group("href").strip()
        if _is_local_ref(href) and href not in seen:
            seen.add(href)
            ordered.append(href)
    return ordered


_EXTERNAL_SCHEME_RE = re.compile(r"^[a-zA-Z][\w+.-]*:|^//|^#", re.ASCII)


def _is_local_ref(src: str) -> bool:
    if not src:
        return False
    return not _EXTERNAL_SCHEME_RE.match(src)


def _safe_join(base: str, name: str) -> Optional[str]:
    """Join ``base`` / ``name`` while refusing path traversal attempts."""

    candidate = os.path.normpath(os.path.join(base, name))
    base_norm = os.path.normpath(base)
    try:
        if os.path.commonpath([candidate, base_norm]) != base_norm:
            return None
    except ValueError:
        return None
    return candidate


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
            "attachments": [_attachment_summary(a) for a in result.attachments],
        }
    if isinstance(result, PushResult):
        return {
            "page_id": result.page_id,
            "title": result.title,
            "version": result.version,
            "attachments": [_attachment_summary(a) for a in result.attachments],
        }
    raise TypeError(f"unsupported result type {type(result).__name__}")


def _attachment_summary(info: AttachmentInfo) -> Dict[str, Any]:
    return {
        "filename": info.filename,
        "path": info.path,
        "media_type": info.media_type,
        "size": info.size,
        "action": info.action,
        "attachment_id": info.attachment_id,
    }
