"""Tests for the file-stream based MCP pull_page / push_page tools."""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from unittest import mock

import pytest

from confluence_markdown_mcp import server as server_module
from confluence_markdown_mcp.service import AttachmentInfo, PullResult, PushResult


# ---------------------------------------------------------------- helpers


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class _FakeService:
    """Minimal ConfluenceService stand-in that records its arguments."""

    def __init__(self, pull_result=None, push_result=None, attachment_files=None):
        self.pull_result = pull_result
        self.push_result = push_result
        self.attachment_files = attachment_files or {}
        self.pull_calls = []
        self.push_calls = []

    def pull_page(self, page_id, output_path=None, download_attachments=True,
                   attachments_dir="attachments"):
        self.pull_calls.append(
            dict(page_id=page_id, output_path=output_path,
                 download_attachments=download_attachments,
                 attachments_dir=attachments_dir)
        )
        # Materialise attachments under the output_path so the server
        # helper can stream them back. The output_path arrives with a
        # trailing separator from server._pull_to_content_blocks.
        if output_path and self.attachment_files:
            att_dir = os.path.join(output_path, attachments_dir)
            os.makedirs(att_dir, exist_ok=True)
            for name, data in self.attachment_files.items():
                with open(os.path.join(att_dir, name), "wb") as fh:
                    fh.write(data)
        return self.pull_result

    def push_page(self, file_path, page_id=None, title=None, upload_attachments=True):
        with open(file_path, "rb") as fh:
            body = fh.read()
        att_dir = os.path.join(os.path.dirname(file_path), "attachments")
        attachments_on_disk = {}
        if os.path.isdir(att_dir):
            for name in os.listdir(att_dir):
                with open(os.path.join(att_dir, name), "rb") as fh:
                    attachments_on_disk[name] = fh.read()
        self.push_calls.append(
            dict(file_path=file_path, page_id=page_id, title=title,
                 upload_attachments=upload_attachments,
                 body=body, attachments=attachments_on_disk)
        )
        return self.push_result


def _registered_tool(app, name):
    """Return the function backing the MCP tool named ``name``."""

    tool = app._tool_manager._tools[name]  # private API but stable in tests
    return tool.fn


# ---------------------------------------------------------------- pull_page


def test_pull_page_returns_metadata_text_and_markdown_blob():
    pull_result = PullResult(
        page_id="123",
        title="Hello",
        space_key="DOC",
        version=4,
        markdown="# Hi\n\nbody 内容",
        path="/tmp/ignored",
        attachments=[],
    )
    fake = _FakeService(pull_result=pull_result)
    app = server_module.create_server(service=fake)

    blocks = _registered_tool(app, "pull_page")(page_id="123")

    assert len(blocks) == 2
    meta_block, body_block = blocks

    # metadata header is plain JSON text and does NOT contain the body
    assert meta_block.type == "text"
    metadata = json.loads(meta_block.text)
    assert metadata["page_id"] == "123"
    assert metadata["title"] == "Hello"
    assert metadata["space_key"] == "DOC"
    assert metadata["version"] == 4
    assert metadata["markdown_resource_uri"] == "confluence://page/123.md"
    assert metadata["markdown_bytes"] == len("# Hi\n\nbody 内容".encode("utf-8"))
    assert metadata["attachments"] == []
    assert "markdown" not in metadata
    assert "body 内容" not in meta_block.text

    # body is shipped as a binary blob, not inline text
    assert body_block.type == "resource"
    assert body_block.resource.mimeType == "text/markdown"
    assert str(body_block.resource.uri) == "confluence://page/123.md"
    assert base64.b64decode(body_block.resource.blob).decode("utf-8") == "# Hi\n\nbody 内容"


def test_pull_page_streams_attachments_as_separate_blobs():
    img_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    pull_result = PullResult(
        page_id="42",
        title="With image",
        space_key="DOC",
        version=2,
        markdown="![image](attachments/pic.png)",
        path=None,  # populated by service in real runs; not used here
        attachments=[
            AttachmentInfo(
                filename="pic.png",
                media_type="image/png",
                size=len(img_bytes),
                action="downloaded",
                attachment_id="att-1",
            ),
        ],
    )
    fake = _FakeService(
        pull_result=pull_result,
        attachment_files={"pic.png": img_bytes},
    )
    app = server_module.create_server(service=fake)

    # Patch attachment.path to point to the file the fake service wrote.
    def patched_pull(page_id, output_path=None, **kw):
        result = _FakeService.pull_page(fake, page_id, output_path=output_path, **kw)
        for att in result.attachments:
            att.path = os.path.join(output_path, "attachments", att.filename)
        return result

    with mock.patch.object(fake, "pull_page", side_effect=patched_pull):
        blocks = _registered_tool(app, "pull_page")(page_id="42")

    assert len(blocks) == 3  # metadata + markdown + 1 attachment
    metadata = json.loads(blocks[0].text)
    assert metadata["attachments"][0]["filename"] == "pic.png"
    assert metadata["attachments"][0]["resource_uri"] == (
        "confluence://page/42/attachment/pic.png"
    )

    att_block = blocks[2]
    assert att_block.type == "resource"
    assert att_block.resource.mimeType == "image/png"
    assert str(att_block.resource.uri) == "confluence://page/42/attachment/pic.png"
    assert base64.b64decode(att_block.resource.blob) == img_bytes


def test_pull_page_respects_download_attachments_flag():
    fake = _FakeService(pull_result=PullResult(
        page_id="1", title="t", space_key="s", version=1, markdown="x"
    ))
    app = server_module.create_server(service=fake)

    _registered_tool(app, "pull_page")(page_id="1", download_attachments=False)

    assert fake.pull_calls[0]["download_attachments"] is False


def test_pull_page_does_not_leak_workdir():
    """The temp dir created for attachment streaming must be cleaned up."""

    fake = _FakeService(pull_result=PullResult(
        page_id="1", title="t", space_key="s", version=1, markdown="x"
    ))
    app = server_module.create_server(service=fake)

    _registered_tool(app, "pull_page")(page_id="1")

    output_path = fake.pull_calls[0]["output_path"]
    assert output_path  # was provided
    assert not os.path.exists(output_path.rstrip(os.sep))


# ---------------------------------------------------------------- push_page


def test_push_page_consumes_markdown_base64_and_calls_service():
    pushed_body = "---\npage_id: \"99\"\ntitle: \"T\"\n---\n\nhello"
    fake = _FakeService(push_result=PushResult(
        page_id="99", title="T", version=7, attachments=[],
    ))
    app = server_module.create_server(service=fake)

    payload = _registered_tool(app, "push_page")(
        markdown_base64=_b64(pushed_body.encode("utf-8")),
        title="T",
    )

    assert payload == {
        "page_id": "99",
        "title": "T",
        "version": 7,
        "attachments": [],
    }
    call = fake.push_calls[0]
    assert call["body"].decode("utf-8") == pushed_body
    assert call["title"] == "T"
    assert call["upload_attachments"] is True
    # workdir cleaned up
    assert not os.path.exists(call["file_path"])


def test_push_page_streams_attachments_to_workdir():
    body = "body"
    img = b"\x00\x01binary\x02"
    fake = _FakeService(push_result=PushResult(
        page_id="1", title="t", version=1,
        attachments=[AttachmentInfo(filename="pic.png", action="created")],
    ))
    app = server_module.create_server(service=fake)

    _registered_tool(app, "push_page")(
        markdown_base64=_b64(body.encode("utf-8")),
        page_id="1",
        attachments=[
            {"filename": "pic.png", "content_base64": _b64(img)},
        ],
    )

    call = fake.push_calls[0]
    assert call["attachments"] == {"pic.png": img}


def test_push_page_rejects_path_traversal_in_attachment_filename():
    fake = _FakeService(push_result=PushResult(
        page_id="1", title="t", version=1, attachments=[],
    ))
    app = server_module.create_server(service=fake)

    with pytest.raises(ValueError, match="unsafe attachment filename"):
        _registered_tool(app, "push_page")(
            markdown_base64=_b64(b"body"),
            page_id="1",
            attachments=[
                {"filename": "../escape.txt", "content_base64": _b64(b"x")},
            ],
        )


def test_push_page_rejects_invalid_base64():
    fake = _FakeService(push_result=PushResult(
        page_id="1", title="t", version=1, attachments=[],
    ))
    app = server_module.create_server(service=fake)

    with pytest.raises(ValueError, match="not valid base64"):
        _registered_tool(app, "push_page")(
            markdown_base64="!!!not-base64!!!",
            page_id="1",
        )


def test_push_page_requires_markdown_payload():
    fake = _FakeService(push_result=PushResult(
        page_id="1", title="t", version=1, attachments=[],
    ))
    app = server_module.create_server(service=fake)

    with pytest.raises(ValueError, match="markdown_base64 is required"):
        _registered_tool(app, "push_page")(markdown_base64="", page_id="1")


# ---------------------------------------------------------------- read_page


def test_read_page_returns_blob_without_attachments_and_without_inline_text():
    pull_result = PullResult(
        page_id="55", title="R", space_key="S", version=1,
        markdown="confidential body",
        attachments=[],
    )
    fake = _FakeService(pull_result=pull_result)
    app = server_module.create_server(service=fake)

    blocks = _registered_tool(app, "read_page")(page_id="55")

    assert fake.pull_calls[0]["download_attachments"] is False
    assert len(blocks) == 2
    # body must not appear in the text block
    assert "confidential body" not in blocks[0].text
    # but is available as a blob
    assert base64.b64decode(blocks[1].resource.blob).decode("utf-8") == "confidential body"
