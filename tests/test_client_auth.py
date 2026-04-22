"""Tests for the cloud vs. server API path selection in :class:`ConfluenceClient`."""

from __future__ import annotations

from unittest import mock

from confluence_markdown_mcp.client import ConfluenceClient
from confluence_markdown_mcp.config import load_settings


def _make_client(is_cloud: bool) -> ConfluenceClient:
    return ConfluenceClient(
        base_url="https://example.atlassian.net",
        email="e@x.com",
        api_token="tok",
        is_cloud=is_cloud,
    )


def test_cloud_uses_wiki_prefix():
    client = _make_client(is_cloud=True)
    with mock.patch.object(ConfluenceClient, "_request", return_value={}) as req:
        client.get_page("1")
    path = req.call_args.args[1]
    assert path.startswith("/wiki/rest/api/content/1")


def test_server_uses_rest_prefix():
    client = _make_client(is_cloud=False)
    with mock.patch.object(ConfluenceClient, "_request", return_value={}) as req:
        client.get_page("1")
        client.update_page("1", "t", "<p/>", 2)
    get_path = req.call_args_list[0].args[1]
    put_path = req.call_args_list[1].args[1]
    assert get_path.startswith("/rest/api/content/1")
    assert put_path == "/rest/api/content/1"
    assert "/wiki/" not in get_path and "/wiki/" not in put_path


def test_settings_is_cloud_default_true(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("CONFLUENCE_EMAIL", "e@x.com")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")
    monkeypatch.delenv("CONFLUENCE_IS_CLOUD", raising=False)
    assert load_settings().is_cloud is True


def test_settings_is_cloud_false(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://wiki.example.com")
    monkeypatch.setenv("CONFLUENCE_EMAIL", "e@x.com")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")
    monkeypatch.setenv("CONFLUENCE_IS_CLOUD", "false")
    s = load_settings()
    assert s.is_cloud is False
    client = ConfluenceClient.from_settings(s)
    assert client._api_prefix == "/rest/api"
