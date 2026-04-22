"""Unit tests for :mod:`confluence_markdown_mcp.service`."""

from __future__ import annotations

import os
import tempfile

from confluence_markdown_mcp.service import _resolve_output_path, _title_to_filename


def test_title_to_filename_strips_unsafe_chars():
    assert _title_to_filename('a/b:c?d*e"f<g>h|i') == "a b c d e f g h i"


def test_title_to_filename_fallback_for_empty_title():
    assert _title_to_filename("") == "page"
    assert _title_to_filename("   ") == "page"


def test_title_to_filename_preserves_cjk():
    assert _title_to_filename("我的 Wiki 页面") == "我的 Wiki 页面"


def test_resolve_output_path_directory_uses_title():
    with tempfile.TemporaryDirectory() as d:
        path = _resolve_output_path(d, None, title="Hello World")
        assert path == os.path.join(d, "Hello World.md")


def test_resolve_output_path_trailing_slash_uses_title():
    path = _resolve_output_path("/tmp/does-not-exist-yet/", None, title="页面标题")
    assert path.endswith(os.sep + "页面标题.md")


def test_resolve_output_path_explicit_filename_is_respected():
    path = _resolve_output_path("/tmp/out.md", None, title="ignored")
    assert path == "/tmp/out.md"
