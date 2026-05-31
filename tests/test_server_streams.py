"""Tests for the local-file-based MCP pull_page / push_page / read_page tools."""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest

from confluence_markdown_mcp import server as server_module
from confluence_markdown_mcp.service import AttachmentInfo, PullResult, PushResult


# ---------------------------------------------------------------- helpers


class _FakeService:
    """Minimal ConfluenceService stand-in that records its arguments."""

    def __init__(self, pull_result=None, push_result=None):
        self.pull_result = pull_result
        self.push_result = push_result
        self.pull_calls = []
        self.push_calls = []

    def pull_page(self, page_id, output_path=None, download_attachments=True,
                   attachments_dir="attachments"):
        self.pull_calls.append(
            dict(page_id=page_id, output_path=output_path,
                 download_attachments=download_attachments,
                 attachments_dir=attachments_dir)
        )
        # When output_path is given, simulate writing a file.
        if output_path:
            os.makedirs(output_path, exist_ok=True)
            file_path = os.path.join(output_path, "Hello.md")
            with open(file_path, "w") as f:
                f.write(self.pull_result.markdown)
            result = PullResult(
                page_id=self.pull_result.page_id,
                title=self.pull_result.title,
                space_key=self.pull_result.space_key,
                version=self.pull_result.version,
                markdown=self.pull_result.markdown,
                path=file_path,
                attachments=self.pull_result.attachments,
            )
            return result
        return self.pull_result

    def push_page(self, file_path, page_id=None, title=None, upload_attachments=True):
        with open(file_path, "rb") as fh:
            body = fh.read()
        self.push_calls.append(
            dict(file_path=file_path, page_id=page_id, title=title,
                 upload_attachments=upload_attachments, body=body)
        )
        return self.push_result


def _registered_tool(app, name):
    """Return the function backing the MCP tool named ``name``."""

    tool = app._tool_manager._tools[name]  # private API but stable in tests
    return tool.fn


# ---------------------------------------------------------------- pull_page


def test_pull_page_without_output_dir_returns_content():
    pull_result = PullResult(
        page_id="123",
        title="Hello",
        space_key="DOC",
        version=4,
        markdown="# Hi\n\nbody 内容",
        path=None,
        attachments=[],
    )
    fake = _FakeService(pull_result=pull_result)
    app = server_module.create_server(service=fake)

    result = _registered_tool(app, "pull_page")(page_id="123")

    assert result["page_id"] == "123"
    assert result["title"] == "Hello"
    assert result["space_key"] == "DOC"
    assert result["version"] == 4
    assert result["content"] == "# Hi\n\nbody 内容"
    assert "file_path" not in result

    # Service was called without output_path
    assert fake.pull_calls[0]["output_path"] is None


def test_pull_page_with_output_dir_saves_file():
    pull_result = PullResult(
        page_id="123",
        title="Hello",
        space_key="DOC",
        version=4,
        markdown="# Hi\n\nbody",
        path=None,
        attachments=[],
    )
    fake = _FakeService(pull_result=pull_result)
    app = server_module.create_server(service=fake)

    with tempfile.TemporaryDirectory() as tmpdir:
        result = _registered_tool(app, "pull_page")(
            page_id="123", output_dir=tmpdir,
        )

    assert result["page_id"] == "123"
    assert result["title"] == "Hello"
    assert result["file_path"] is not None
    assert "content" not in result

    # Service was called with output_path ending in separator
    call = fake.pull_calls[0]
    assert call["output_path"].endswith(os.sep)
    assert call["download_attachments"] is True


# ---------------------------------------------------------------- push_page


def test_push_page_reads_local_file():
    fake = _FakeService(push_result=PushResult(
        page_id="99", title="T", version=7, attachments=[],
    ))
    app = server_module.create_server(service=fake)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("---\npage_id: \"99\"\ntitle: \"T\"\n---\n\nhello")
        f.flush()
        tmp_path = f.name

    try:
        result = _registered_tool(app, "push_page")(file_path=tmp_path, title="T")

        assert result == {
            "page_id": "99",
            "title": "T",
            "version": 7,
        }
        call = fake.push_calls[0]
        assert call["file_path"] == tmp_path
        assert call["title"] == "T"
        assert call["upload_attachments"] is True
    finally:
        os.unlink(tmp_path)


def test_push_page_rejects_missing_file():
    fake = _FakeService(push_result=PushResult(
        page_id="1", title="t", version=1, attachments=[],
    ))
    app = server_module.create_server(service=fake)

    with pytest.raises(ValueError, match="not a valid file"):
        _registered_tool(app, "push_page")(file_path="/nonexistent/file.md")


def test_push_page_rejects_empty_path():
    fake = _FakeService(push_result=PushResult(
        page_id="1", title="t", version=1, attachments=[],
    ))
    app = server_module.create_server(service=fake)

    with pytest.raises(ValueError, match="not a valid file"):
        _registered_tool(app, "push_page")(file_path="")


# ---------------------------------------------------------------- read_page


def test_read_page_returns_content_without_file_write():
    pull_result = PullResult(
        page_id="55", title="R", space_key="S", version=1,
        markdown="confidential body",
        attachments=[],
    )
    fake = _FakeService(pull_result=pull_result)
    app = server_module.create_server(service=fake)

    result = _registered_tool(app, "read_page")(page_id="55")

    assert result["page_id"] == "55"
    assert result["title"] == "R"
    assert result["content"] == "confidential body"
    # Service was called without output_path
    assert fake.pull_calls[0]["output_path"] is None
