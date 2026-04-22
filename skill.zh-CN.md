---
name: confluence-markdown
description: 通过 MCP 服务器在 Confluence wiki 页面与本地 Markdown 文件之间进行同步。可用于将页面拉到本地进行编辑，或将本地 Markdown 的改动推送回已有的 wiki 页面。
version: 0.2.0
---

# confluence-markdown skill（中文版）

本技能向支持 MCP 的助手说明如何与 `confluence-markdown-mcp` 服务器协作。

## 何时使用

当用户希望执行以下操作时调用本技能：

- **读取 / 获取** 某个 Confluence 页面，用于编辑、摘要或引用
  （需写入磁盘时使用 `pull_page` 并传入 `output_dir`；若仅需内容，使用
  `read_page`）。
- **编辑并发布** 本地 Markdown 的改动到 Confluence
  （使用 `push_page`，并提供精确的文件路径与 `page_id`）。
- **在线预览** 页面 —— 访问 `confluence://page/{page_id}` 资源。

**不要** 用本技能创建全新的页面或管理附件，这些功能目前不在支持范围内。

## 前置条件

服务器从环境变量读取凭证。首次调用前请与用户确认以下变量已正确设置：

- `CONFLUENCE_BASE_URL` —— 例如 `https://<tenant>.atlassian.net`
- `CONFLUENCE_EMAIL`    —— Atlassian 账号邮箱
- `CONFLUENCE_API_TOKEN` —— Atlassian API Token

可选：

- `CONFLUENCE_TIMEOUT`        —— HTTP 超时（秒），默认 `30`
- `CONFLUENCE_MARKDOWN_DIR`   —— 相对 `output_dir` 的默认根目录

## 提供的工具

### `pull_page(page_id: string, output_dir?: string)`

下载一个 Confluence 页面。若传入 `output_dir`，它 **必须是一个目录** ——
Markdown 文件名由服务器根据页面标题自动生成（不安全字符会被剥离），
因此调用方不应传入完整文件路径。生成的文件包含 YAML 风格的 front matter
（`page_id`、`title`、`space_key`、`version`），响应中还会返回
`markdown_preview` 与解析后的 `path`。若未提供 `output_dir`，则在 `markdown`
字段中直接返回完整正文。

### `push_page(file_path: string, page_id?: string, title?: string)`

将本地 Markdown 文件上传回 Confluence。如果文件本身的 front matter 中已包含
`page_id`（`pull_page` 会自动写入），则可以省略参数中的 `page_id`。`title`
默认使用 front matter 中的标题，或页面当前的标题。

### `read_page(page_id: string)`

`pull_page` 的便捷封装，**不会** 写入磁盘，直接返回 Markdown 正文和基础
元数据。

## 推荐工作流

1. 向用户询问 Confluence 页面 ID（以及可选的本地路径）。
2. 调用 `pull_page` 并指定 `output_dir`，向用户确认新文件的位置
   （文件名由服务器根据页面标题生成）。
3. 提出 Markdown 修改建议，并请用户在上传前进行评审。
4. 使用相同的 `file_path` 调用 `push_page`，并向用户展示返回的新 `version`。

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
- `ConfluenceError: (401 Unauthorized)` → API Token 无效或已过期。
- `ConfluenceError: (404 Not Found)` → 检查 `page_id` 是否正确。
- `push_page` 抛出 `FileNotFoundError` → 检查文件的绝对路径是否存在。

每次 `push_page` 成功后，请将返回的 version 号告知用户，以便确认更新。
