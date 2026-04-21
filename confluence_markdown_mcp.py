#!/usr/bin/env python3
import argparse
import base64
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


class _StorageToMarkdownParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.link_stack = []
        self.pre_depth = 0
        self.heading_depth = 0
        self.list_depth = 0
        self.in_list_item = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading_depth = int(tag[1])
            self.parts.append("\n" + "#" * self.heading_depth + " ")
        elif tag == "p":
            self.parts.append("\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag in ("strong", "b"):
            self.parts.append("**")
        elif tag in ("em", "i"):
            self.parts.append("*")
        elif tag == "code" and self.pre_depth == 0:
            self.parts.append("`")
        elif tag == "pre":
            self.pre_depth += 1
            self.parts.append("\n```\n")
        elif tag == "a":
            self.parts.append("[")
            self.link_stack.append(attrs_dict.get("href", ""))
        elif tag in ("ul", "ol"):
            self.list_depth += 1
        elif tag == "li":
            self.in_list_item = True
            indent = "  " * max(self.list_depth - 1, 0)
            self.parts.append(f"\n{indent}- ")

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading_depth = 0
            self.parts.append("\n")
        elif tag == "p":
            self.parts.append("\n")
        elif tag in ("strong", "b"):
            self.parts.append("**")
        elif tag in ("em", "i"):
            self.parts.append("*")
        elif tag == "code" and self.pre_depth == 0:
            self.parts.append("`")
        elif tag == "pre":
            self.pre_depth = max(self.pre_depth - 1, 0)
            self.parts.append("\n```\n")
        elif tag == "a":
            href = self.link_stack.pop() if self.link_stack else ""
            self.parts.append(f"]({href})")
        elif tag in ("ul", "ol"):
            self.list_depth = max(self.list_depth - 1, 0)
        elif tag == "li":
            self.in_list_item = False

    def handle_data(self, data):
        self.parts.append(data)

    def markdown(self):
        text = "".join(self.parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n"


class ConfluenceClient:
    def __init__(self, base_url: str, email: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        auth = f"{email}:{api_token}".encode("utf-8")
        self.auth_header = "Basic " + base64.b64encode(auth).decode("utf-8")

    def _request(self, method: str, path: str, payload=None):
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self.auth_header,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8")
            raise RuntimeError(f"Confluence API error ({exc.code}): {detail}") from exc

    def get_page(self, page_id: str):
        expand = urllib.parse.quote("body.storage,version,space")
        path = f"/wiki/rest/api/content/{page_id}?expand={expand}"
        return self._request("GET", path)

    def update_page(self, page_id: str, title: str, storage_value: str, version: int):
        payload = {
            "id": str(page_id),
            "type": "page",
            "title": title,
            "version": {"number": version},
            "body": {"storage": {"value": storage_value, "representation": "storage"}},
        }
        return self._request("PUT", f"/wiki/rest/api/content/{page_id}", payload)


def storage_to_markdown(storage_html: str) -> str:
    parser = _StorageToMarkdownParser()
    parser.feed(storage_html or "")
    parser.close()
    return parser.markdown()


def markdown_to_storage(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    out = []
    in_code = False
    code_buf = []
    list_open = False

    def close_list():
        nonlocal list_open
        if list_open:
            out.append("</ul>")
            list_open = False

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            if not in_code:
                close_list()
                in_code = True
                code_buf = []
            else:
                escaped = html.escape("\n".join(code_buf))
                out.append(f"<pre><code>{escaped}</code></pre>")
                in_code = False
            continue
        if in_code:
            code_buf.append(raw)
            continue
        if not line.strip():
            close_list()
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            close_list()
            level = len(heading.group(1))
            content = _inline_markdown_to_html(heading.group(2).strip())
            out.append(f"<h{level}>{content}</h{level}>")
            continue

        list_item = re.match(r"^\s*-\s+(.*)$", line)
        if list_item:
            if not list_open:
                out.append("<ul>")
                list_open = True
            content = _inline_markdown_to_html(list_item.group(1).strip())
            out.append(f"<li>{content}</li>")
            continue

        close_list()
        out.append(f"<p>{_inline_markdown_to_html(line.strip())}</p>")

    close_list()
    if in_code:
        escaped = html.escape("\n".join(code_buf))
        out.append(f"<pre><code>{escaped}</code></pre>")

    return "".join(out)


def _inline_markdown_to_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\[(.+?)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped


def dump_markdown_file(path: str, page: dict, markdown_body: str):
    version = page.get("version", {}).get("number", 1)
    space_key = page.get("space", {}).get("key", "")
    title = page.get("title", "")
    page_id = page.get("id", "")
    content = (
        "---\n"
        f"page_id: {page_id}\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        f"space_key: {space_key}\n"
        f"version: {version}\n"
        "---\n\n"
        f"{markdown_body.rstrip()}\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def load_markdown_file(path: str):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    metadata = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            front = text[4:end].splitlines()
            body = text[end + 5 :]
            for line in front:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()
                if value.startswith('"') and value.endswith('"'):
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        pass
                metadata[key] = value
    return metadata, body


def _client_from_env() -> ConfluenceClient:
    base_url = os.getenv("CONFLUENCE_BASE_URL", "").strip()
    email = os.getenv("CONFLUENCE_EMAIL", "").strip()
    token = os.getenv("CONFLUENCE_API_TOKEN", "").strip()
    if not base_url or not email or not token:
        raise RuntimeError(
            "Missing Confluence credentials. Please set CONFLUENCE_BASE_URL, "
            "CONFLUENCE_EMAIL, and CONFLUENCE_API_TOKEN."
        )
    return ConfluenceClient(base_url=base_url, email=email, api_token=token)


def cmd_read(args):
    client = _client_from_env()
    page = client.get_page(args.page_id)
    storage = page.get("body", {}).get("storage", {}).get("value", "")
    markdown_body = storage_to_markdown(storage)
    print(markdown_body, end="")


def cmd_export(args):
    client = _client_from_env()
    page = client.get_page(args.page_id)
    storage = page.get("body", {}).get("storage", {}).get("value", "")
    markdown_body = storage_to_markdown(storage)
    dump_markdown_file(args.output, page, markdown_body)
    print(f"Exported page {args.page_id} to {args.output}")


def cmd_upload(args):
    client = _client_from_env()
    metadata, markdown_body = load_markdown_file(args.file)
    page_id = args.page_id or metadata.get("page_id")
    if not page_id:
        raise RuntimeError("Page ID not found. Provide --page-id or include page_id in front matter.")

    current = client.get_page(str(page_id))
    current_version = int(current.get("version", {}).get("number", 1))
    title = args.title or metadata.get("title") or current.get("title")
    storage = markdown_to_storage(markdown_body)
    updated = client.update_page(str(page_id), title, storage, current_version + 1)
    new_version = updated.get("version", {}).get("number", current_version + 1)
    print(f"Uploaded {args.file} to page {page_id} (version {new_version})")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Read Confluence wiki pages, export to local markdown, edit, and upload back."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_read = subparsers.add_parser("read", help="Read a Confluence wiki page and print markdown")
    p_read.add_argument("--page-id", required=True, help="Confluence page ID")
    p_read.set_defaults(func=cmd_read)

    p_export = subparsers.add_parser("export", help="Export a Confluence page to markdown file")
    p_export.add_argument("--page-id", required=True, help="Confluence page ID")
    p_export.add_argument("--output", required=True, help="Output markdown file path")
    p_export.set_defaults(func=cmd_export)

    p_upload = subparsers.add_parser("upload", help="Upload local markdown file back to Confluence")
    p_upload.add_argument("--file", required=True, help="Local markdown file path")
    p_upload.add_argument("--page-id", help="Override target page ID")
    p_upload.add_argument("--title", help="Override page title")
    p_upload.set_defaults(func=cmd_upload)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
