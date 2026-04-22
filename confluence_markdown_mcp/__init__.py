"""Confluence <-> Markdown MCP package.

Public entry points:

* :func:`confluence_markdown_mcp.server.run` – start the MCP stdio server.
* :func:`confluence_markdown_mcp.cli.main` – command line interface.
"""

from .config import Settings, load_settings
from .client import ConfluenceClient
from .converter import storage_to_markdown, markdown_to_storage
from .files import dump_markdown_file, load_markdown_file

__all__ = [
    "Settings",
    "load_settings",
    "ConfluenceClient",
    "storage_to_markdown",
    "markdown_to_storage",
    "dump_markdown_file",
    "load_markdown_file",
]

__version__ = "0.2.0"
