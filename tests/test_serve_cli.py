"""Tests for the ``serve`` CLI subcommand."""

from __future__ import annotations

from unittest import mock

import pytest

from confluence_markdown_mcp import cli, server


def test_serve_defaults_to_stdio():
    args = cli.build_parser().parse_args(["serve"])
    assert args.command == "serve"


@pytest.mark.parametrize(
    "option",
    ["--transport", "--host", "--port", "--mount-path", "--sse-path"],
)
def test_serve_rejects_http_options(option):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["serve", option, "value"])


def test_cmd_serve_runs_stdio_server():
    with mock.patch.object(server, "run") as run_mock:
        rc = cli.main(["serve"])
    assert rc == 0
    run_mock.assert_called_once_with()


@pytest.mark.parametrize("transport", ["websocket", "sse", "streamable-http"])
def test_run_rejects_non_stdio_transport(transport):
    with pytest.raises(ValueError):
        server.run(transport=transport)


def test_run_invokes_fastmcp_with_transport():
    fake_app = mock.MagicMock()
    with mock.patch.object(server, "create_server", return_value=fake_app) as create_mock:
        server.run()
    create_mock.assert_called_once_with()
    fake_app.run.assert_called_once_with(transport="stdio")


def test_create_server_does_not_configure_http_transports():
    with mock.patch.object(server, "FastMCP") as fastmcp_cls:
        server.create_server()
    kwargs = fastmcp_cls.call_args.kwargs
    assert "host" not in kwargs
    assert "port" not in kwargs
    assert "mount_path" not in kwargs
    assert "sse_path" not in kwargs
    assert "streamable_http_path" not in kwargs
    assert "json_response" not in kwargs
    assert "stateless_http" not in kwargs
