"""Allow ``python -m confluence_markdown_mcp`` to invoke the CLI."""

try:
    from .cli import main
except ImportError:  # pragma: no cover - fallback when executed as a script path
    from confluence_markdown_mcp.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
