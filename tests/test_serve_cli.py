"""Tests for the ``serve`` CLI subcommand and HTTP transport wiring."""

from __future__ import annotations

from unittest import mock

import pytest

from confluence_markdown_mcp import cli, server


def test_serve_defaults_to_stdio():
    args = cli.build_parser().parse_args(["serve"])
    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.mount_path == "/"
    assert args.sse_path == "/sse"
    assert args.streamable_http_path == "/mcp"
    assert args.json_response is False
    assert args.stateless_http is False


def test_serve_parses_http_options():
    args = cli.build_parser().parse_args(
        [
            "serve",
            "--transport",
            "streamable-http",
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
            "--mount-path",
            "/api",
            "--sse-path",
            "/events",
            "--streamable-http-path",
            "/rpc",
            "--json-response",
            "--stateless-http",
        ]
    )
    assert args.transport == "streamable-http"
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.mount_path == "/api"
    assert args.sse_path == "/events"
    assert args.streamable_http_path == "/rpc"
    assert args.json_response is True
    assert args.stateless_http is True


def test_serve_rejects_unknown_transport():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["serve", "--transport", "websocket"])


def test_cmd_serve_forwards_options_to_run():
    with mock.patch.object(server, "run") as run_mock:
        rc = cli.main(
            [
                "serve",
                "--transport",
                "sse",
                "--host",
                "0.0.0.0",
                "--port",
                "1234",
                "--mount-path",
                "/m",
                "--sse-path",
                "/s",
                "--streamable-http-path",
                "/h",
                "--json-response",
                "--stateless-http",
            ]
        )
    assert rc == 0
    run_mock.assert_called_once_with(
        transport="sse",
        host="0.0.0.0",
        port=1234,
        mount_path="/m",
        sse_path="/s",
        streamable_http_path="/h",
        json_response=True,
        stateless_http=True,
    )


def test_run_rejects_unknown_transport():
    with pytest.raises(ValueError):
        server.run(transport="websocket")


def test_run_invokes_fastmcp_with_transport():
    fake_app = mock.MagicMock()
    with mock.patch.object(server, "create_server", return_value=fake_app) as create_mock:
        server.run(
            transport="streamable-http",
            host="0.0.0.0",
            port=8123,
            stateless_http=True,
        )
    create_mock.assert_called_once()
    kwargs = create_mock.call_args.kwargs
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 8123
    assert kwargs["stateless_http"] is True
    fake_app.run.assert_called_once_with(transport="streamable-http")


def test_create_server_passes_http_settings_to_fastmcp():
    with mock.patch.object(server, "FastMCP") as fastmcp_cls:
        server.create_server(
            host="0.0.0.0",
            port=9000,
            mount_path="/api",
            sse_path="/events",
            streamable_http_path="/rpc",
            json_response=True,
            stateless_http=True,
        )
    kwargs = fastmcp_cls.call_args.kwargs
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 9000
    assert kwargs["mount_path"] == "/api"
    assert kwargs["sse_path"] == "/events"
    assert kwargs["streamable_http_path"] == "/rpc"
    assert kwargs["json_response"] is True
    assert kwargs["stateless_http"] is True
