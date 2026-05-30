---
name: confluence-markdown
description: Exchange Confluence wiki pages with Markdown streams through an MCP server. Use it to fetch a page for editing, or push reviewed Markdown changes back to an existing wiki page.
version: 0.2.0
---

# confluence-markdown skill

This skill teaches an MCP-capable assistant how to work with the
`confluence-markdown-mcp` server.

> 🇨🇳 中文版本见 [`skill.zh-CN.md`](skill.zh-CN.md)。

## When to use

Invoke this skill whenever the user wants to:

- **Read / fetch** a Confluence wiki page for editing, summarisation or
  quotation (use `pull_page` to get metadata + Markdown/attachment blobs, or
  `read_page` if you only need the Markdown blob).
- **Edit and publish** Markdown changes back to Confluence (use
  `push_page` with base64-encoded Markdown, plus `page_id` when needed).
- Preview a page inline – access the `confluence://page/{page_id}` resource.

Do **not** use it for creating brand-new pages; that is out of scope in the
current version. In MCP mode the server speaks stdio and returns file-like
blobs; the host may choose to save them locally, but the tool contract does
not take local filesystem paths.

## Prerequisites

The server reads credentials from environment variables. Confirm with the
user that the following are set before the first call:

- `CONFLUENCE_BASE_URL` – e.g. `https://<tenant>.atlassian.net`
- `CONFLUENCE_EMAIL` + `CONFLUENCE_API_TOKEN` – Atlassian account email and API token for Basic auth
- `CONFLUENCE_PAT` – Personal Access Token for Bearer auth

Optional:

- `CONFLUENCE_TIMEOUT`        – HTTP timeout in seconds (default `30`)

Authentication is either/or: use email + API token for Basic auth, or set
`CONFLUENCE_PAT` for Bearer auth. If both are present, the PAT is used.

## Tools provided

### `pull_page(page_id: string, download_attachments?: boolean = true)`

Downloads a Confluence page and returns a short JSON metadata text block
plus one `text/markdown` `EmbeddedResource` blob for the page body. When
`download_attachments` is true, each downloaded attachment is returned as
an additional `EmbeddedResource` blob. The Markdown body is not returned
inline as plain text.

### `push_page(markdown_base64: string, page_id?: string, title?: string, upload_attachments?: boolean = true, attachments?: [{filename, content_base64}, ...])`

Uploads Markdown back to Confluence from caller-supplied file streams. The
Markdown body must be base64-encoded in `markdown_base64`. `page_id` may
be omitted if the decoded Markdown carries it in front matter. `title`
defaults to the front-matter title or the page's current title.

Attachment upload rules on push:

- Image references are uploaded automatically, for example
  `![image](attachments/example.png)`.
- Ordinary file links are uploaded only when the link is immediately
  followed by `<!--cm-attachment-->`, for example
  `[file](attachments/example.eml) <!--cm-attachment-->`.
- The marker must come **after** the link. `<!--cm-attachment-->[file](...)`
  is not recognised.
- URL-encoded local paths are decoded before matching and naming the
  attachment, so an encoded path and the decoded local filename stay in
  sync on upload and in the page body.

### `read_page(page_id: string)`

Convenience wrapper around `pull_page` that always skips attachment
downloads. It still returns metadata + one Markdown `EmbeddedResource`
blob rather than inline Markdown text.

## Recommended workflow

1. Ask the user for the Confluence page ID.
2. Call `pull_page` (or `read_page`) and let the MCP host decide whether
   to save the returned blobs as local files.
3. Propose Markdown edits; have the user review before uploading.
4. Call `push_page` with base64-encoded Markdown (and optional attachment
   blobs); display the returned new `version`.

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
| Inline `<u>`, `<s>`/`<del>`, `<ins>`, `<sub>`, `<sup>`, `<br>` | Same tag verbatim |
| `html` / `html-bobswift` macro (embedded `<iframe>`, e.g. drawio / diagrams.net) | Raw HTML body is unwrapped into a Markdown `<iframe …></iframe>` line; push re-wraps it in `html-bobswift` automatically |
| Any other `<ac:structured-macro>` | HTML comment token that round-trips |

Because unknown macros are preserved as comments, **do not delete them** in
an edit unless the user explicitly asks to remove that block.

### drawio / iframe embeds

Confluence renders drawio / diagrams.net diagrams through an `<iframe>`
inside an `html-bobswift` (or `html`) user macro. On pull the iframe is
unwrapped onto a single Markdown line; on push the server re-wraps it in
the same macro so Confluence can render it. Iframe `src` attributes are
restricted to `http` / `https` URLs and non-allow-listed attributes
(`onload`, `srcdoc`, `sandbox`, …) are dropped so unsafe embeds cannot
leak through a round-trip.

## Error handling

- `RuntimeError: Missing Confluence credentials...` → remind the user to
  export the required environment variables.
- `ConfluenceError: (401 Unauthorized)` → the API token or PAT is invalid, expired, or lacks permission.
- `ConfluenceError: (404 Not Found)` → double-check the `page_id`.
- `ValueError` on `push_page` → verify that `markdown_base64` /
  `attachments[].content_base64` are valid base64 and filenames are safe.

Always surface the returned version number after a `push_page` call so the
user can confirm the update.
