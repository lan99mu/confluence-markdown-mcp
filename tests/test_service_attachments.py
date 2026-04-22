"""Tests for attachment pull / push orchestration in :mod:`service`."""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional

from confluence_markdown_mcp.service import ConfluenceService, AttachmentInfo


class FakeClient:
    """In-memory stand-in for :class:`ConfluenceClient`."""

    def __init__(
        self,
        page: Dict[str, Any],
        attachments: Optional[List[Dict[str, Any]]] = None,
        attachment_bodies: Optional[Dict[str, bytes]] = None,
    ) -> None:
        self.page = page
        self.attachments = attachments or []
        self.attachment_bodies = attachment_bodies or {}
        self.update_calls: List[Dict[str, Any]] = []
        self.uploads: List[Dict[str, Any]] = []
        self.downloads: List[str] = []

    def get_page(self, page_id: str) -> Dict[str, Any]:
        return self.page

    def update_page(
        self, page_id: str, title: str, storage: str, version: int
    ) -> Dict[str, Any]:
        self.update_calls.append(
            {"page_id": page_id, "title": title, "storage": storage, "version": version}
        )
        return {"id": page_id, "title": title, "version": {"number": version}}

    def list_attachments(self, page_id: str, page_size: int = 100) -> List[Dict[str, Any]]:
        return list(self.attachments)

    def download_attachment(self, download_path: str, dest_path: str) -> str:
        self.downloads.append(download_path)
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
        body = self.attachment_bodies.get(download_path, b"fake-bytes")
        with open(dest_path, "wb") as fh:
            fh.write(body)
        return dest_path

    def create_attachment(
        self, page_id: str, file_path: str, comment: Optional[str] = None
    ) -> Dict[str, Any]:
        filename = os.path.basename(file_path)
        self.uploads.append({"action": "create", "file": file_path, "comment": comment})
        return {"results": [{"id": f"att-{filename}", "title": filename}]}

    def update_attachment_data(
        self,
        page_id: str,
        attachment_id: str,
        file_path: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.uploads.append(
            {
                "action": "update",
                "attachment_id": attachment_id,
                "file": file_path,
                "comment": comment,
            }
        )
        return {"id": attachment_id}


def _make_page(storage: str) -> Dict[str, Any]:
    return {
        "id": "42",
        "title": "Demo page",
        "space": {"key": "DOC"},
        "version": {"number": 1},
        "body": {"storage": {"value": storage}},
    }


def test_pull_downloads_referenced_attachments():
    storage = (
        '<p><ac:image><ri:attachment ri:filename="hello.png" /></ac:image></p>'
    )
    attachments = [
        {
            "id": "123",
            "title": "hello.png",
            "_links": {"download": "/download/attachments/42/hello.png?version=1"},
            "extensions": {"mediaType": "image/png", "fileSize": 10},
        }
    ]
    client = FakeClient(
        _make_page(storage),
        attachments=attachments,
        attachment_bodies={"/download/attachments/42/hello.png?version=1": b"x" * 10},
    )
    service = ConfluenceService(client=client, settings=None)

    with tempfile.TemporaryDirectory() as d:
        result = service.pull_page(page_id="42", output_path=d + os.sep)

        assert result.path is not None
        assert os.path.exists(result.path)
        dir_ = os.path.dirname(result.path)
        downloaded = os.path.join(dir_, "attachments", "hello.png")
        assert os.path.exists(downloaded)
        assert len(result.attachments) == 1
        assert result.attachments[0].action == "downloaded"
        assert result.attachments[0].filename == "hello.png"
        assert result.attachments[0].media_type == "image/png"


def test_pull_skips_attachment_when_size_matches():
    storage = (
        '<p><ac:image><ri:attachment ri:filename="hello.png" /></ac:image></p>'
    )
    attachments = [
        {
            "id": "123",
            "title": "hello.png",
            "_links": {"download": "/download/attachments/42/hello.png"},
            "extensions": {"mediaType": "image/png", "fileSize": 3},
        }
    ]
    client = FakeClient(_make_page(storage), attachments=attachments)
    service = ConfluenceService(client=client, settings=None)

    with tempfile.TemporaryDirectory() as d:
        # Pre-seed a matching file so the pull should skip the download.
        os.makedirs(os.path.join(d, "attachments"), exist_ok=True)
        with open(os.path.join(d, "attachments", "hello.png"), "wb") as fh:
            fh.write(b"abc")
        result = service.pull_page(page_id="42", output_path=d + os.sep)

        assert client.downloads == []
        assert result.attachments[0].action == "skipped"


def test_pull_reports_missing_remote_attachment():
    storage = (
        '<p><ac:image><ri:attachment ri:filename="gone.png" /></ac:image></p>'
    )
    client = FakeClient(_make_page(storage), attachments=[])
    service = ConfluenceService(client=client, settings=None)

    with tempfile.TemporaryDirectory() as d:
        result = service.pull_page(page_id="42", output_path=d + os.sep)
        assert result.attachments[0].action == "missing"


def test_push_uploads_new_local_attachments():
    client = FakeClient({"id": "42", "title": "t", "version": {"number": 3}})
    service = ConfluenceService(client=client, settings=None)

    with tempfile.TemporaryDirectory() as d:
        # Prepare Markdown file + referenced attachment.
        att_dir = os.path.join(d, "attachments")
        os.makedirs(att_dir)
        with open(os.path.join(att_dir, "cat.png"), "wb") as fh:
            fh.write(b"binary")
        md_path = os.path.join(d, "page.md")
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write("---\npage_id: \"42\"\ntitle: \"t\"\n---\n\n")
            fh.write("![cat](attachments/cat.png)\n")

        result = service.push_page(file_path=md_path)
        assert len(result.attachments) == 1
        info = result.attachments[0]
        assert info.filename == "cat.png"
        assert info.action == "created"
        assert client.uploads[0]["action"] == "create"
        # Page update must happen after the attachment upload.
        assert client.update_calls


def test_push_skips_unchanged_attachment():
    client = FakeClient(
        {"id": "42", "title": "t", "version": {"number": 3}},
        attachments=[
            {
                "id": "att-1",
                "title": "cat.png",
                "extensions": {"fileSize": 6, "mediaType": "image/png"},
            }
        ],
    )
    service = ConfluenceService(client=client, settings=None)

    with tempfile.TemporaryDirectory() as d:
        att_dir = os.path.join(d, "attachments")
        os.makedirs(att_dir)
        with open(os.path.join(att_dir, "cat.png"), "wb") as fh:
            fh.write(b"binary")
        md_path = os.path.join(d, "page.md")
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write("---\npage_id: \"42\"\n---\n\n![cat](attachments/cat.png)\n")

        result = service.push_page(file_path=md_path)
        assert result.attachments[0].action == "skipped"
        assert client.uploads == []


def test_push_updates_changed_attachment():
    client = FakeClient(
        {"id": "42", "title": "t", "version": {"number": 3}},
        attachments=[
            {
                "id": "att-1",
                "title": "cat.png",
                "extensions": {"fileSize": 999, "mediaType": "image/png"},
            }
        ],
    )
    service = ConfluenceService(client=client, settings=None)

    with tempfile.TemporaryDirectory() as d:
        att_dir = os.path.join(d, "attachments")
        os.makedirs(att_dir)
        with open(os.path.join(att_dir, "cat.png"), "wb") as fh:
            fh.write(b"binary")
        md_path = os.path.join(d, "page.md")
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write("---\npage_id: \"42\"\n---\n\n![cat](attachments/cat.png)\n")

        result = service.push_page(file_path=md_path)
        assert result.attachments[0].action == "updated"
        assert client.uploads[0]["action"] == "update"


def test_push_rejects_path_traversal_outside_markdown_dir():
    client = FakeClient({"id": "42", "title": "t", "version": {"number": 3}})
    service = ConfluenceService(client=client, settings=None)

    with tempfile.TemporaryDirectory() as d:
        md_path = os.path.join(d, "page.md")
        # Reference escapes the markdown directory.
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write("---\npage_id: \"42\"\n---\n\n")
            fh.write("![x](../escape.png)\n")

        result = service.push_page(file_path=md_path)
        # No attachment should be uploaded, no entry recorded.
        assert client.uploads == []
        assert result.attachments == []


def test_pull_ignores_attachments_when_disabled():
    storage = (
        '<p><ac:image><ri:attachment ri:filename="hello.png" /></ac:image></p>'
    )
    attachments = [
        {"id": "1", "title": "hello.png", "_links": {"download": "/dl"}}
    ]
    client = FakeClient(_make_page(storage), attachments=attachments)
    service = ConfluenceService(client=client, settings=None)
    with tempfile.TemporaryDirectory() as d:
        result = service.pull_page(
            page_id="42", output_path=d + os.sep, download_attachments=False
        )
        assert result.attachments == []
        assert client.downloads == []


def test_push_ignores_attachments_when_disabled():
    client = FakeClient({"id": "42", "title": "t", "version": {"number": 3}})
    service = ConfluenceService(client=client, settings=None)
    with tempfile.TemporaryDirectory() as d:
        att_dir = os.path.join(d, "attachments")
        os.makedirs(att_dir)
        with open(os.path.join(att_dir, "cat.png"), "wb") as fh:
            fh.write(b"binary")
        md_path = os.path.join(d, "page.md")
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write("---\npage_id: \"42\"\n---\n\n![cat](attachments/cat.png)\n")
        result = service.push_page(file_path=md_path, upload_attachments=False)
        assert result.attachments == []
        assert client.uploads == []
