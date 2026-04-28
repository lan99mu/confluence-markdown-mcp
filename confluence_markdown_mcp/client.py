"""Thin Confluence REST client built on the Python standard library."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, Iterable, List, Optional

from .config import Settings


class ConfluenceError(RuntimeError):
    """Raised when the Confluence REST API returns a non-2xx response."""


class ConfluenceClient:
    """Minimal Confluence Cloud REST client.

    Only the subset of endpoints required by this project is implemented –
    fetching and updating a page.  The client has **no** third-party
    dependencies so it can be used in sandboxed/minimal environments.
    """

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        timeout: float = 30.0,
        is_cloud: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.is_cloud = is_cloud
        # Cloud instances serve the REST API under `/wiki/rest/api`, while
        # Server / Data Center installations use `/rest/api` directly.
        self._api_prefix = "/wiki/rest/api" if is_cloud else "/rest/api"
        auth = f"{email}:{api_token}".encode("utf-8")
        self._auth_header = "Basic " + base64.b64encode(auth).decode("utf-8")

    # ------------------------------------------------------------------ utils
    @classmethod
    def from_settings(cls, settings: Settings) -> "ConfluenceClient":
        return cls(
            base_url=settings.base_url,
            email=settings.email,
            api_token=settings.api_token,
            timeout=settings.timeout,
            is_cloud=settings.is_cloud,
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self._auth_header,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            detail = re.sub(r"\s+", " ", detail).strip()
            if len(detail) > 500:
                detail = detail[:500] + "..."
            raise ConfluenceError(
                f"Confluence API error ({exc.code} {exc.reason}): {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ConfluenceError(
                f"Network error calling Confluence: {exc.reason}"
            ) from exc

    # ------------------------------------------------------------------ API
    def get_page(self, page_id: str) -> Dict[str, Any]:
        """Fetch a page with body (storage format), version and space info."""

        expand = urllib.parse.quote("body.storage,version,space")
        path = f"{self._api_prefix}/content/{page_id}?expand={expand}"
        return self._request("GET", path)

    def update_page(
        self,
        page_id: str,
        title: str,
        storage_value: str,
        version: int,
    ) -> Dict[str, Any]:
        """Replace the page body with ``storage_value`` (storage-format XHTML)."""

        payload = {
            "id": str(page_id),
            "type": "page",
            "title": title,
            "version": {"number": version},
            "body": {
                "storage": {
                    "value": storage_value,
                    "representation": "storage",
                }
            },
        }
        return self._request("PUT", f"{self._api_prefix}/content/{page_id}", payload)

    # ------------------------------------------------------------ attachments
    def list_attachments(
        self,
        page_id: str,
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        """Return every attachment attached to ``page_id`` (auto-paginated)."""

        results: List[Dict[str, Any]] = []
        start = 0
        while True:
            qs = urllib.parse.urlencode(
                {"limit": page_size, "start": start, "expand": "version"}
            )
            path = f"{self._api_prefix}/content/{page_id}/child/attachment?{qs}"
            response = self._request("GET", path)
            batch = response.get("results") or []
            results.extend(batch)
            if len(batch) < page_size:
                break
            start += page_size
        return results

    def download_attachment(self, download_path: str, dest_path: str) -> str:
        """Stream an attachment to ``dest_path`` and return the resolved path.

        ``download_path`` is typically the ``_links.download`` value from
        :meth:`list_attachments` – a Confluence-relative URL such as
        ``/download/attachments/123/foo.png?version=1``.  Absolute URLs
        are accepted as well.
        """

        if download_path.startswith(("http://", "https://")):
            url = download_path
        else:
            # Cloud download links are rooted at /wiki/download/… whereas
            # ``_links.download`` on a page response returns a path that is
            # relative to the wiki context.  ``urljoin`` handles both
            # absolute and context-relative forms.
            base_prefix = "/wiki" if self.is_cloud else ""
            if base_prefix and download_path.startswith(base_prefix):
                # Path already includes the /wiki prefix – use as-is.
                url = f"{self.base_url}{download_path}"
            elif download_path.startswith("/"):
                url = f"{self.base_url}{base_prefix}{download_path}"
            else:
                url = f"{self.base_url}{base_prefix}/{download_path}"

        headers = {
            "Authorization": self._auth_header,
            "Accept": "*/*",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp, open(
                dest_path, "wb"
            ) as fh:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
        except urllib.error.HTTPError as exc:  # pragma: no cover - network errors
            detail = exc.read().decode("utf-8", errors="replace")
            detail = re.sub(r"\s+", " ", detail).strip()
            if len(detail) > 500:
                detail = detail[:500] + "..."
            raise ConfluenceError(
                f"Confluence download error ({exc.code} {exc.reason}): {detail}"
            ) from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network errors
            raise ConfluenceError(
                f"Network error downloading attachment: {exc.reason}"
            ) from exc
        return dest_path

    def create_attachment(
        self,
        page_id: str,
        file_path: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload ``file_path`` as a new attachment on the given page."""

        return self._upload_attachment(
            f"{self._api_prefix}/content/{page_id}/child/attachment",
            file_path,
            comment=comment,
        )

    def update_attachment_data(
        self,
        page_id: str,
        attachment_id: str,
        file_path: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Replace the binary data for an existing attachment."""

        return self._upload_attachment(
            f"{self._api_prefix}/content/{page_id}/child/attachment/{attachment_id}/data",
            file_path,
            comment=comment,
        )

    # ------------------------------------------------------------------ internal
    def _upload_attachment(
        self,
        path: str,
        file_path: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST a multipart/form-data upload and return the parsed response."""

        filename = os.path.basename(file_path)
        mime_type, _ = mimetypes.guess_type(filename)
        mime_type = mime_type or "application/octet-stream"
        with open(file_path, "rb") as fh:
            content = fh.read()

        fields: List["tuple[str, str]"] = [("minorEdit", "true")]
        if comment:
            fields.append(("comment", comment))
        body, content_type = _encode_multipart(fields, filename, content, mime_type)

        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self._auth_header,
            "Accept": "application/json",
            "Content-Type": content_type,
            # Required by Confluence to accept attachment uploads.
            "X-Atlassian-Token": "no-check",
        }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            detail = re.sub(r"\s+", " ", detail).strip()
            if len(detail) > 500:
                detail = detail[:500] + "..."
            raise ConfluenceError(
                f"Confluence upload error ({exc.code} {exc.reason}): {detail}"
            ) from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network errors
            raise ConfluenceError(
                f"Network error uploading attachment: {exc.reason}"
            ) from exc


def _encode_multipart(
    fields: Iterable["tuple[str, str]"],
    filename: str,
    content: bytes,
    content_type: str,
) -> "tuple[bytes, str]":
    """Encode ``fields`` and a single ``file`` part as multipart/form-data.

    Returns ``(body_bytes, content_type_header)``.  The implementation is
    intentionally minimal because we only need a couple of text fields
    plus one binary upload; pulling in ``requests`` just for this would
    violate the "standard library only" constraint of this client.
    """

    boundary = "----cmmcp-" + uuid.uuid4().hex
    crlf = b"\r\n"
    lines: List[bytes] = []
    for name, value in fields:
        lines.append(f"--{boundary}".encode())
        lines.append(
            f'Content-Disposition: form-data; name="{name}"'.encode()
        )
        lines.append(b"")
        lines.append(value.encode("utf-8"))
    # Escape backslashes / quotes in the filename per RFC 7578 rules so
    # a crafted name can't break out of the header.
    safe_name = filename.replace("\\", "\\\\").replace('"', '\\"')
    lines.append(f"--{boundary}".encode())
    lines.append(
        f'Content-Disposition: form-data; name="file"; filename="{safe_name}"'.encode()
    )
    lines.append(f"Content-Type: {content_type}".encode())
    lines.append(b"")
    lines.append(content)
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")
    body = crlf.join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


def file_sha1(path: str) -> str:
    """Return the SHA-1 hex digest of ``path`` (used for upload skip checks)."""

    digest = hashlib.sha1(usedforsecurity=False)  # content identity, not crypto
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
