"""Tests for the FastAPI HTTP API."""

from __future__ import annotations

import io
import os
from typing import Any, Dict, List, Optional

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from confluence_markdown_mcp.http_api import create_app  # noqa: E402
from confluence_markdown_mcp.service import (  # noqa: E402
    AttachmentInfo,
    PullResult,
    PushResult,
)


class _FakeClient:
    """Stand-in for ConfluenceClient with the bits the HTTP layer uses."""

    def __init__(self, attachments=None, downloads=None):
        self.attachments = attachments or []
        self.downloads = downloads or {}
        self.download_calls: List[str] = []

    def list_attachments(self, page_id: str):
        return list(self.attachments)

    def download_attachment(self, download_url: str, dest_path: str) -> str:
        self.download_calls.append(download_url)
        data = self.downloads.get(download_url, b"")
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
        with open(dest_path, "wb") as fh:
            fh.write(data)
        return dest_path


class _FakeService:
    def __init__(
        self,
        pull_result: Optional[PullResult] = None,
        push_result: Optional[PushResult] = None,
        attachment_files: Optional[Dict[str, bytes]] = None,
        client: Optional[_FakeClient] = None,
    ):
        self.pull_result = pull_result
        self.push_result = push_result
        self.attachment_files = attachment_files or {}
        self.client = client or _FakeClient()
        self.pull_calls: List[Dict[str, Any]] = []
        self.push_calls: List[Dict[str, Any]] = []

    def pull_page(
        self,
        page_id: str,
        output_path: Optional[str] = None,
        download_attachments: bool = True,
        attachments_dir: str = "attachments",
    ) -> PullResult:
        self.pull_calls.append(
            dict(
                page_id=page_id,
                output_path=output_path,
                download_attachments=download_attachments,
                attachments_dir=attachments_dir,
            )
        )
        if output_path and self.attachment_files:
            att_dir = os.path.join(output_path, attachments_dir)
            os.makedirs(att_dir, exist_ok=True)
            for name, data in self.attachment_files.items():
                with open(os.path.join(att_dir, name), "wb") as fh:
                    fh.write(data)
        return self.pull_result

    def push_page(
        self,
        file_path: str,
        page_id: Optional[str] = None,
        title: Optional[str] = None,
        upload_attachments: bool = True,
    ) -> PushResult:
        with open(file_path, "rb") as fh:
            body = fh.read()
        att_dir = os.path.join(os.path.dirname(file_path), "attachments")
        attachments_on_disk: Dict[str, bytes] = {}
        if os.path.isdir(att_dir):
            for name in os.listdir(att_dir):
                with open(os.path.join(att_dir, name), "rb") as fh:
                    attachments_on_disk[name] = fh.read()
        self.push_calls.append(
            dict(
                file_path=file_path,
                page_id=page_id,
                title=title,
                upload_attachments=upload_attachments,
                body=body,
                attachments=attachments_on_disk,
            )
        )
        return self.push_result


# ---------------------------------------------------------------- health


def test_healthz_returns_ok():
    app = create_app(service=_FakeService())
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


# ---------------------------------------------------------------- GET /pages


def test_get_page_returns_metadata_markdown_and_attachment_manifest():
    img = b"\x89PNG\r\nfake"
    pull_result = PullResult(
        page_id="123",
        title="Hello",
        space_key="DOC",
        version=4,
        markdown="# Hi\n\nbody",
        attachments=[
            AttachmentInfo(
                filename="pic.png",
                media_type="image/png",
                size=len(img),
                action="downloaded",
                attachment_id="att-1",
            ),
        ],
    )
    fake = _FakeService(
        pull_result=pull_result,
        attachment_files={"pic.png": img},
    )
    with TestClient(create_app(service=fake)) as client:
        response = client.get("/pages/123")

    assert response.status_code == 200
    body = response.json()
    assert body["page_id"] == "123"
    assert body["title"] == "Hello"
    assert body["version"] == 4
    assert body["markdown"] == "# Hi\n\nbody"
    assert body["attachments"][0]["filename"] == "pic.png"
    assert body["attachments"][0]["download_url"] == "/pages/123/attachments/pic.png"
    assert fake.pull_calls[0]["download_attachments"] is True


def test_get_page_respects_download_attachments_false():
    pull_result = PullResult(
        page_id="1", title="t", space_key="s", version=1, markdown="x"
    )
    fake = _FakeService(pull_result=pull_result)
    with TestClient(create_app(service=fake)) as client:
        response = client.get("/pages/1", params={"download_attachments": "false"})

    assert response.status_code == 200
    assert fake.pull_calls[0]["download_attachments"] is False
    assert fake.pull_calls[0]["output_path"] is None


def test_get_page_markdown_returns_plain_markdown():
    pull_result = PullResult(
        page_id="9",
        title="t",
        space_key="s",
        version=1,
        markdown="# header\n\ncontent 内容",
    )
    fake = _FakeService(pull_result=pull_result)
    with TestClient(create_app(service=fake)) as client:
        response = client.get("/pages/9/markdown")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text == "# header\n\ncontent 内容"


# ------------------------------------------------ GET /pages/.../attachments


def test_get_attachment_streams_bytes_from_client():
    data = b"\x00\x01\x02fake-bytes"
    fake_client = _FakeClient(
        attachments=[
            {
                "title": "pic.png",
                "_links": {"download": "/download/attachments/1/pic.png"},
                "extensions": {"mediaType": "image/png"},
            }
        ],
        downloads={"/download/attachments/1/pic.png": data},
    )
    fake = _FakeService(client=fake_client)
    with TestClient(create_app(service=fake)) as client:
        response = client.get("/pages/1/attachments/pic.png")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == data
    assert fake_client.download_calls == ["/download/attachments/1/pic.png"]


def test_get_attachment_404_when_missing():
    fake = _FakeService(client=_FakeClient(attachments=[]))
    with TestClient(create_app(service=fake)) as client:
        response = client.get("/pages/1/attachments/missing.png")
    assert response.status_code == 404


def test_get_attachment_rejects_path_traversal():
    fake = _FakeService(client=_FakeClient(attachments=[]))
    with TestClient(create_app(service=fake)) as client:
        response = client.get("/pages/1/attachments/..%2Fescape.txt")
    # Either FastAPI's router strips the traversal, or our handler rejects
    # it with a 400; both are acceptable security-wise.
    assert response.status_code in (400, 404)


# ---------------------------------------------------------------- POST /pages


def test_post_page_accepts_multipart_md_and_attachments():
    pushed_result = PushResult(
        page_id="42",
        title="T",
        version=8,
        attachments=[AttachmentInfo(filename="pic.png", action="created")],
    )
    fake = _FakeService(push_result=pushed_result)

    md_body = b"---\npage_id: \"42\"\ntitle: \"T\"\n---\n\nhello"
    img = b"binary\x00\x01"

    with TestClient(create_app(service=fake)) as client:
        response = client.post(
            "/pages/42",
            data={"title": "T", "upload_attachments": "true"},
            files=[
                ("md_file", ("doc.md", io.BytesIO(md_body), "text/markdown")),
                ("attachments", ("pic.png", io.BytesIO(img), "image/png")),
            ],
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["page_id"] == "42"
    assert payload["version"] == 8
    assert payload["attachments"][0]["filename"] == "pic.png"

    call = fake.push_calls[0]
    assert call["page_id"] == "42"
    assert call["title"] == "T"
    assert call["upload_attachments"] is True
    assert call["body"] == md_body
    assert call["attachments"] == {"pic.png": img}
    # workdir cleaned up
    assert not os.path.exists(call["file_path"])


def test_post_page_requires_md_file():
    fake = _FakeService(
        push_result=PushResult(page_id="1", title="t", version=1, attachments=[])
    )
    with TestClient(create_app(service=fake)) as client:
        response = client.post("/pages/1", data={"title": "x"})
    assert response.status_code in (400, 422)


def test_post_page_rejects_empty_md_file():
    fake = _FakeService(
        push_result=PushResult(page_id="1", title="t", version=1, attachments=[])
    )
    with TestClient(create_app(service=fake)) as client:
        response = client.post(
            "/pages/1",
            files=[("md_file", ("doc.md", io.BytesIO(b""), "text/markdown"))],
        )
    assert response.status_code == 400


def test_post_page_rejects_attachment_with_path_traversal():
    fake = _FakeService(
        push_result=PushResult(page_id="1", title="t", version=1, attachments=[])
    )
    with TestClient(create_app(service=fake)) as client:
        response = client.post(
            "/pages/1",
            files=[
                ("md_file", ("doc.md", io.BytesIO(b"body"), "text/markdown")),
                (
                    "attachments",
                    ("../escape.txt", io.BytesIO(b"x"), "application/octet-stream"),
                ),
            ],
        )
    assert response.status_code == 400


def test_post_page_skips_attachment_upload_when_flag_false():
    fake = _FakeService(
        push_result=PushResult(page_id="1", title="t", version=1, attachments=[])
    )
    with TestClient(create_app(service=fake)) as client:
        response = client.post(
            "/pages/1",
            data={"upload_attachments": "false"},
            files=[("md_file", ("doc.md", io.BytesIO(b"body"), "text/markdown"))],
        )
    assert response.status_code == 200
    assert fake.push_calls[0]["upload_attachments"] is False


def test_post_page_invalid_bool_returns_400():
    fake = _FakeService(
        push_result=PushResult(page_id="1", title="t", version=1, attachments=[])
    )
    with TestClient(create_app(service=fake)) as client:
        response = client.post(
            "/pages/1",
            data={"upload_attachments": "maybe"},
            files=[("md_file", ("doc.md", io.BytesIO(b"body"), "text/markdown"))],
        )
    assert response.status_code == 400
