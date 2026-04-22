"""Thin Confluence REST client built on the Python standard library."""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
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
        path = f"/wiki/rest/api/content/{page_id}?expand={expand}"
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
        return self._request("PUT", f"/wiki/rest/api/content/{page_id}", payload)
