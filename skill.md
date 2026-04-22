---
name: confluence-markdown
description: Sync Confluence wiki pages with local Markdown files through an MCP server. Use it to pull a page down for editing, or push local Markdown edits back to an existing wiki page.
version: 0.2.0
---

# confluence-markdown skill

This skill teaches an MCP-capable assistant how to work with the
`confluence-markdown-mcp` server.

## When to use

Invoke this skill whenever the user wants to:

- **Read / fetch** a Confluence wiki page for editing, summarisation or
  quotation (use `pull_page` with `output_dir`, or `read_page` if you only
  need the content).
- **Edit and publish** local Markdown changes back to Confluence (use
  `push_page` with the exact path and `page_id`).
- Preview a page inline – access the `confluence://page/{page_id}` resource.

Do **not** use it for creating brand-new pages or managing attachments; those
are out of scope in the current version.

## Prerequisites

The server reads credentials from environment variables. Confirm with the
user that the following are set before the first call:

- `CONFLUENCE_BASE_URL` – e.g. `https://<tenant>.atlassian.net`
- `CONFLUENCE_EMAIL`    – Atlassian account email
- `CONFLUENCE_API_TOKEN` – Atlassian API token

Optional:

- `CONFLUENCE_TIMEOUT`        – HTTP timeout in seconds (default `30`)
- `CONFLUENCE_MARKDOWN_DIR`   – default root for relative `output_dir`s

## Tools provided

### `pull_page(page_id: string, output_dir?: string)`

Downloads a Confluence page. When `output_dir` is provided it **must be a
directory** – the Markdown file name is generated automatically by the
server from the page title (unsafe characters are stripped), so the
caller should never pass a full file path. The resulting file contains
YAML-style front matter (`page_id`, `title`, `space_key`, `version`) and
the response includes `markdown_preview` plus the resolved `path`.
Without `output_dir`, the full Markdown body is returned in `markdown`.

### `push_page(file_path: string, page_id?: string, title?: string)`

Uploads a local Markdown file back to Confluence. The target `page_id` may
be omitted if the file carries it in its front matter (which `pull_page`
writes automatically). `title` defaults to the front-matter title or the
page's current title.

### `read_page(page_id: string)`

Convenience wrapper around `pull_page` that never writes to disk – returns
the Markdown body plus basic metadata.

## Recommended workflow

1. Ask the user for the Confluence page ID (and optional local path).
2. Call `pull_page` with an `output_dir`; confirm the new file location
   (the filename is produced from the page title by the server).
3. Propose Markdown edits; have the user review before uploading.
4. Call `push_page` with the same `file_path`; display the returned new
   `version`.

## Formatting guarantees

The server handles the following Confluence storage-format constructs when
converting to Markdown, and reverses the process on upload:

| Storage format | Markdown |
| --- | --- |
| `code` macro (with language + CDATA) | Fenced code block ```` ```lang ```` |
| `info` / `note` / `warning` / `tip` | `> [!INFO]` blockquote admonition |
| `<table>` with `<th>/<td>` | Pipe table (first row as header) |
| `<ul>/<ol>/<li>` (nested) | `-` / `1.` list (2-space indent) |
| `<ac:task-list>` with `<ac:task>` | `- [ ]` / `- [x]` task items |
| `<a>` / `<img>` | `[text](url)` / `![alt](src)` |
| `<span style="color: …; background-color: …">` | Same `<span>` verbatim |
| `<p style="text-align: left/right/center/justify">` | Same `<p>` verbatim |
| Inline `<u>`, `<s>`/`<del>`, `<sub>`, `<sup>`, `<br>` | Same tag verbatim |
| Any other `<ac:structured-macro>` | HTML comment token that round-trips |

Because unknown macros are preserved as comments, **do not delete them** in
an edit unless the user explicitly asks to remove that block.

## Error handling

- `RuntimeError: Missing Confluence credentials...` → remind the user to
  export the required environment variables.
- `ConfluenceError: (401 Unauthorized)` → the API token is invalid/expired.
- `ConfluenceError: (404 Not Found)` → double-check the `page_id`.
- `FileNotFoundError` on `push_page` → verify the absolute file path.

Always surface the returned version number after a `push_page` call so the
user can confirm the update.
