"""Command line interface.

Exposes three sub-commands:

* ``pull``  – download a page to Markdown (file or stdout).
* ``push``  – upload a Markdown file back to Confluence.
* ``serve`` – run the MCP stdio server.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .service import ConfluenceService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confluence-markdown-mcp",
        description=(
            "Pull Confluence wiki pages to local Markdown, push edits back, "
            "and expose both operations over the Model Context Protocol."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_pull = sub.add_parser("pull", help="Pull a Confluence page as Markdown")
    p_pull.add_argument("--page-id", required=True, help="Confluence page ID")
    p_pull.add_argument(
        "--output",
        "-o",
        help=(
            "Path for the generated markdown file. If omitted the markdown "
            "is printed to stdout."
        ),
    )
    p_pull.add_argument(
        "--no-attachments",
        dest="download_attachments",
        action="store_false",
        help="Do not download referenced attachments (images / files).",
    )
    p_pull.add_argument(
        "--attachments-dir",
        default="attachments",
        help=(
            "Directory (relative to the markdown file) where attachments "
            "are stored. Defaults to 'attachments'."
        ),
    )
    p_pull.set_defaults(func=_cmd_pull, download_attachments=True)

    p_push = sub.add_parser("push", help="Push a local Markdown file to Confluence")
    p_push.add_argument("--file", "-f", required=True, help="Path to markdown file")
    p_push.add_argument(
        "--page-id",
        help="Target page ID (defaults to the one stored in the file's front matter)",
    )
    p_push.add_argument("--title", help="Override the page title")
    p_push.add_argument(
        "--no-attachments",
        dest="upload_attachments",
        action="store_false",
        help="Do not upload local files referenced by the markdown.",
    )
    p_push.set_defaults(func=_cmd_push, upload_attachments=True)

    p_serve = sub.add_parser("serve", help="Start the MCP server (stdio transport)")
    p_serve.set_defaults(func=_cmd_serve)

    return parser


def _cmd_pull(args: argparse.Namespace) -> int:
    service = ConfluenceService()
    result = service.pull_page(
        page_id=args.page_id,
        output_path=args.output,
        download_attachments=args.download_attachments,
        attachments_dir=args.attachments_dir,
    )
    if args.output:
        print(
            f"Pulled page {result.page_id} ({result.title}) → {result.path} "
            f"[v{result.version}]",
            file=sys.stderr,
        )
        for att in result.attachments:
            print(
                f"  attachment: {att.filename} ({att.action})",
                file=sys.stderr,
            )
    else:
        sys.stdout.write(result.markdown)
    return 0


def _cmd_push(args: argparse.Namespace) -> int:
    service = ConfluenceService()
    result = service.push_page(
        file_path=args.file,
        page_id=args.page_id,
        title=args.title,
        upload_attachments=args.upload_attachments,
    )
    print(
        f"Pushed {args.file} to page {result.page_id} ({result.title}) "
        f"[v{result.version}]",
        file=sys.stderr,
    )
    for att in result.attachments:
        print(
            f"  attachment: {att.filename} ({att.action})",
            file=sys.stderr,
        )
    return 0


def _cmd_serve(_: argparse.Namespace) -> int:
    # Imported lazily so that the ``pull`` / ``push`` commands work even if
    # the optional ``mcp`` dependency is missing.
    from .server import run as run_server

    run_server()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except Exception as exc:  # pragma: no cover - top level error reporting
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
