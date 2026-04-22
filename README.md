# confluence-markdown-mcp

一个轻量级的 **Confluence ⇄ 本地 Markdown** 同步工具，同时提供 **MCP (Model
Context Protocol)** 服务端，可直接挂接到 Claude Desktop、Continue、Cursor 等
支持 MCP 的客户端中使用。

主要特性：

- 🧩 **基于最新 MCP 框架** (`mcp.server.fastmcp`) 实现 stdio 服务端
- 🔐 **全部配置走环境变量**，无需在代码或命令行里写入 token
- ⬇️ **Pull**：把 Confluence 页面拉取到本地为 `.md` 文件（带 front matter）
- ⬆️ **Push**：把本地 `.md` 文件按 `pageId` 上传回 Wiki
- 🧱 **正确处理特殊样式区块**：代码宏、`info/note/warning/tip` 提示框、表格、
  列表、链接、图片，以及未知宏（保留并原样回写）
- 🧭 **分层/模块化**：`config` / `client` / `converter` / `files` / `service` /
  `server` / `cli` 各司其职，代码可读性高、便于扩展
- 🧪 带有基础单元测试，保证格式往返稳定

## 目录结构

```
confluence_markdown_mcp/
├── __init__.py          # 包入口，汇出主要 API
├── __main__.py          # 支持 `python -m confluence_markdown_mcp`
├── cli.py               # 命令行：pull / push / serve
├── config.py            # 环境变量读取与校验
├── client.py            # Confluence REST 客户端（仅依赖标准库）
├── converter/
│   ├── __init__.py
│   ├── macros.py        # 处理 <ac:structured-macro>：code / info / ...
│   ├── storage_to_md.py # Confluence 存储格式 → Markdown
│   └── md_to_storage.py # Markdown → Confluence 存储格式
├── files.py             # 带 front matter 的 markdown 文件读写
├── server.py            # MCP 服务端（FastMCP）
└── service.py           # 业务编排层（供 CLI 与 MCP 复用）
skill.md                 # 默认 MCP skill 描述
tests/                   # 单元测试
```

## 安装

### 通过 pip 从源码安装

```bash
git clone https://github.com/lan99mu/confluence-markdown-mcp.git
cd confluence-markdown-mcp
pip install .
```

安装后会提供 `confluence-markdown-mcp` 命令以及 Python 包
`confluence_markdown_mcp`。

### 开发模式（含测试依赖）

```bash
pip install -e ".[dev]"
pytest
```

### 直接以模块方式运行（不安装）

```bash
pip install -r requirements.txt
python -m confluence_markdown_mcp --help
```

## 配置（环境变量）

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `CONFLUENCE_BASE_URL` | ✅ | Wiki 根 URL，例如 `https://<your-domain>.atlassian.net` |
| `CONFLUENCE_EMAIL` | ✅ | 调用 API 的账号邮箱 |
| `CONFLUENCE_API_TOKEN` | ✅ | [API token](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `CONFLUENCE_TIMEOUT` | ❎ | HTTP 超时秒数，默认 `30` |
| `CONFLUENCE_MARKDOWN_DIR` | ❎ | `pull` 时相对路径所依赖的默认目录 |
| `CONFLUENCE_IS_CLOUD` | ❎ | 是否为 Confluence Cloud；默认 `true`。设为 `false` 时走 Server/Data Center 的 `/rest/api`；Cloud 走 `/wiki/rest/api` |

```bash
export CONFLUENCE_BASE_URL="https://example.atlassian.net"
export CONFLUENCE_EMAIL="you@example.com"
export CONFLUENCE_API_TOKEN="xxxxxxxxxxxx"
```

## 命令行用法

```bash
# 拉取到 stdout
confluence-markdown-mcp pull --page-id 123456

# 拉取到文件（自动带 front matter）
confluence-markdown-mcp pull --page-id 123456 -o ./docs/my-page.md

# 拉取到目录：文件名自动使用 wiki 页面 title（非法字符会被替换）
confluence-markdown-mcp pull --page-id 123456 -o ./docs/

# 回写到 wiki（页面 ID 取自 front matter 或 --page-id）
confluence-markdown-mcp push --file ./docs/my-page.md
confluence-markdown-mcp push --file ./docs/my-page.md --page-id 123456 --title "新标题"

# 启动 MCP stdio 服务
confluence-markdown-mcp serve
```

## 作为 MCP 服务使用

启动：`confluence-markdown-mcp serve`（stdio 传输）。服务提供以下工具：

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `pull_page` | `page_id`, `output_dir?` | 拉取为 Markdown；`output_dir` 仅指定目录（文件名由服务端按页面标题自动生成），不传则直接返回内容 |
| `push_page` | `file_path`, `page_id?`, `title?` | 上传本地 `.md` 到 wiki |
| `read_page` | `page_id` | 仅返回 Markdown（不落盘） |

资源：`confluence://page/{page_id}` — 只读 Markdown 视图。

### Claude Desktop / 通用 MCP 客户端配置

在客户端的 MCP 配置文件中加入：

```json
{
  "mcpServers": {
    "confluence-markdown": {
      "command": "confluence-markdown-mcp",
      "args": ["serve"],
      "env": {
        "CONFLUENCE_BASE_URL": "https://example.atlassian.net",
        "CONFLUENCE_EMAIL": "you@example.com",
        "CONFLUENCE_API_TOKEN": "xxxxxxxxxxxx"
      }
    }
  }
}
```

如未安装为命令，也可以这样启动：

```json
{
  "command": "python",
  "args": ["-m", "confluence_markdown_mcp", "serve"]
}
```

## 特殊样式区块的处理

| Confluence 结构 | Markdown 表现 |
| --- | --- |
| `<ac:structured-macro ac:name="code">` + `<ac:plain-text-body><![CDATA[...]]>` | 带语言标识的 ``` 代码块 |
| `<ac:structured-macro ac:name="info/note/warning/tip">` | `> [!INFO]` 风格的 GFM admonition 引用块 |
| `<table>` + `<th>/<td>` | 标准管道分隔表格（首行作表头） |
| `<ul>/<ol>/<li>` | `-` / `1.` 列表，支持嵌套（两空格缩进） |
| `<ac:task-list>` / `<ac:task>` | GFM 任务列表：`- [ ] body` / `- [x] body` |
| `<span style="color:…">` / `<font color="…">` | 原样保留 `<span>` 行内 HTML，色值保持 |
| `<a href=...>` / `<img>` | `[text](url)` / `![alt](src)` |
| 其他未知 `<ac:structured-macro>` | 保留为 HTML 注释 token，上传时原样还原 |

代码块内容使用 `CDATA` 原样保存；对 `]]>` 序列做了分片处理以避免 XML 解析错误。

### 已知不支持的 Markdown 输入形态

为了避免误判普通段落，`md → storage` 的表格识别遵循标准 GFM 规范，
要求表头行、分隔行（`| --- | --- |`）与每一条数据行**各占一行**，单元格内
**不得包含换行或空行**。下列由其它工具导出或手写拼接的 "Confluence 风味"
输入目前不会被识别为表格，会被降级按段落 + 列表 + 任务列表渲染（内容不会
丢失，但外层不会生成 `<table>`）：

```
| 是否有架构设计：
- [ ] 有，架构设计地址是：https://example.com/...
- [ ] 无，原因是：

本人已充分理解架构设计方案：
- [ ] 一致
- [ ] 不一致，原因是： | | --- |
```

典型特征：

- 表头单元格内嵌入了多行内容（甚至空行），关闭的 `|` 出现在若干行之后；
- 分隔行 `| --- |` 与最后一行数据粘在同一行（`… | | --- |`），而非独立一行。

如果需要表格外观，请将其改写为单行单元格的标准 Markdown 表格（必要时使用
`<br>` 表示单元格内换行），或者直接在 Confluence 上以原生表格编辑。

## 开发与测试

```bash
pip install -e ".[dev]"
pytest
```

## 许可证

MIT（见 [LICENSE](LICENSE)）。
