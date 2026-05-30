---
name: confluence-markdown
description: 通过 MCP 服务器以 Markdown 流的形式交换 Confluence wiki 页面。可用于拉取页面供编辑，或将审核后的 Markdown 改动推送回已有的 wiki 页面。
version: 0.2.0
---

# confluence-markdown skill（中文版）

本技能向支持 MCP 的助手说明如何与 `confluence-markdown-mcp` 服务器协作。

## 何时使用

当用户希望执行以下操作时调用本技能：

- **读取 / 获取** 某个 Confluence 页面，用于编辑、摘要或引用
  （使用 `pull_page` 获取元数据 + Markdown/附件 blob；若只需 Markdown，
  使用 `read_page`）。
- **编辑并发布** Markdown 改动到 Confluence
  （使用 `push_page`，传入 base64 编码后的 Markdown；必要时再传 `page_id`）。
- **在线预览** 页面 —— 访问 `confluence://page/{page_id}` 资源。

**不要** 用本技能创建全新的页面，这一功能目前不在支持范围内。MCP 模式仍是
本地 stdio 调用：服务端返回的是文件型 blob，是否保存为本地文件由 host 决定，
工具契约本身不接收本地文件路径。

## 前置条件

服务器从环境变量读取凭证。首次调用前请与用户确认以下变量已正确设置：

- `CONFLUENCE_BASE_URL` —— 例如 `https://<tenant>.atlassian.net`
- `CONFLUENCE_EMAIL` + `CONFLUENCE_API_TOKEN` —— Basic Auth 所需的账号邮箱与 API Token
- `CONFLUENCE_PAT` —— Personal Access Token，走 Bearer Auth

可选：

- `CONFLUENCE_TIMEOUT`        —— HTTP 超时（秒），默认 `30`

认证方式二选一：使用 `CONFLUENCE_EMAIL` + `CONFLUENCE_API_TOKEN`，或设置 `CONFLUENCE_PAT`。若两者同时存在，优先使用 PAT。

## 提供的工具

### `pull_page(page_id: string, download_attachments?: boolean = true)`

下载一个 Confluence 页面，返回一个简短的 JSON 元数据文本块，以及一个
`text/markdown` 的 `EmbeddedResource` blob 作为正文。若
`download_attachments` 为 true，则每个已下载附件还会各自返回一个
`EmbeddedResource` blob。Markdown 正文不会以内联纯文本返回。

### `push_page(markdown_base64: string, page_id?: string, title?: string, upload_attachments?: boolean = true, attachments?: [{filename, content_base64}, ...])`

将调用方提供的 Markdown 文件流上传回 Confluence。正文必须以
`markdown_base64` 传入。若解码后的 Markdown front matter 中已包含
`page_id`，则可以省略参数中的 `page_id`。`title` 默认使用 front matter
中的标题，或页面当前的标题。

附件上传规则：

- 图片引用会自动上传，例如 `![image](attachments/example.png)`。
- 普通文件链接只有在链接后紧跟 `<!--cm-attachment-->` 时才会作为附件上传，例如
  `[file](attachments/example.eml) <!--cm-attachment-->`。
- marker 必须写在链接**后面**；`<!--cm-attachment-->[file](...)` 不会被识别。
- URL 编码的本地路径会在匹配文件和生成附件名之前先 decode，因此编码路径与本地
  中文文件名、空格等场景会保持一致。

### `read_page(page_id: string)`

`pull_page` 的便捷封装，总是跳过附件下载；返回的仍是元数据 + 一个 Markdown
`EmbeddedResource` blob，而不是内联正文文本。

## 推荐工作流

1. 向用户询问 Confluence 页面 ID。
2. 调用 `pull_page`（或 `read_page`），并让 MCP host 自行决定是否把返回的
   blob 保存为本地文件。
3. 提出 Markdown 修改建议，并请用户在上传前进行评审。
4. 调用 `push_page`，传入 base64 编码后的 Markdown（以及可选附件 blob），并向
   用户展示返回的新 `version`。

## 格式保留能力

服务器在将 Confluence 的 storage 格式转换为 Markdown 时，会识别以下结构；
上传时则执行相反的转换：

| Storage 格式 | Markdown |
| --- | --- |
| `code` 宏（含语言 + CDATA） | 围栏代码块 ```` ```lang ```` |
| `info` / `note` / `warning` / `tip` | `> [!INFO]` 风格的告示型引用 |
| `<table>` 与 `<th>`/`<td>` | 管道表格（第一行作表头） |
| `<ul>`/`<ol>`/`<li>`（支持嵌套） | `-` / `1.` 列表（2 空格缩进） |
| `<ac:task-list>` 与 `<ac:task>` | `- [ ]` / `- [x]` 任务项 |
| `<a>` / `<img>` | `[文本](url)` / `![alt](src)` |
| `<span style="color: …; background-color: …">` | 原样保留同一个 `<span>` |
| `<p style="text-align: left/right/center/justify">` | 原样保留同一个 `<p>` |
| 行内 `<u>`、`<s>`/`<del>`、`<ins>`、`<sub>`、`<sup>`、`<br>` | 原样保留相同标签 |
| `html` / `html-bobswift` 宏（含 `<iframe>` 嵌入，例如 drawio / diagrams.net） | 把 HTML 原样展开为 Markdown 中的 `<iframe …></iframe>` 行；上传时自动重新用 `html-bobswift` 宏包装 |
| 其他 `<ac:structured-macro>` | 可往返的 HTML 注释占位符 |

由于未知宏以注释形式保留，**除非用户明确要求删除**，否则 **不要** 在编辑
过程中移除它们。

### 关于 drawio 等 iframe 嵌入

Confluence 中的 drawio / diagrams.net 图通常通过 `html-bobswift` 宏包裹一个
`<iframe>` 嵌入。拉取时，服务器会把 iframe 解包到 Markdown 中作为单独一行；
上传时，会自动重新包裹为 `html-bobswift` 宏，使页面能正常渲染。
`src` 属性仅允许 `http` / `https` 协议，未通过白名单的 URL 或属性都会被
丢弃，避免引入不安全的嵌入。

## 错误处理

- `RuntimeError: Missing Confluence credentials...` → 提醒用户导出必需的
  环境变量。
- `ConfluenceError: (401 Unauthorized)` → API Token 或 PAT 无效、已过期，或权限不足。
- `ConfluenceError: (404 Not Found)` → 检查 `page_id` 是否正确。
- `push_page` 抛出 `ValueError` → 检查 `markdown_base64` /
  `attachments[].content_base64` 是否为合法 base64，且附件文件名是否安全。

每次 `push_page` 成功后，请将返回的 version 号告知用户，以便确认更新。
