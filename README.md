# confluence-markdown-mcp

一个轻量级 Confluence Wiki 本地化 Markdown 工具，支持：

- 读取 Confluence Wiki 页面内容
- 导出为本地 Markdown 文件（可在本地编辑）
- 将本地编辑后的 Markdown 上传回 Confluence Wiki

## 使用方式

### 1) 配置环境变量

```bash
export CONFLUENCE_BASE_URL="https://<your-domain>.atlassian.net"
export CONFLUENCE_EMAIL="<your-email>"
export CONFLUENCE_API_TOKEN="<your-api-token>"
```

### 2) 读取 Wiki（输出为 Markdown）

```bash
python confluence_markdown_mcp.py read --page-id 123456
```

### 3) 导出 Wiki 到本地 Markdown

```bash
python confluence_markdown_mcp.py export --page-id 123456 --output ./wiki-page.md
```

导出的文件会包含 front matter（如 `page_id`、`title`），方便后续回传。

### 4) 本地编辑后上传回 Wiki

```bash
python confluence_markdown_mcp.py upload --file ./wiki-page.md
```

也可以在上传时覆盖页面 ID 或标题：

```bash
python confluence_markdown_mcp.py upload --file ./wiki-page.md --page-id 123456 --title "新标题"
```
