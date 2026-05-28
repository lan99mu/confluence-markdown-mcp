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
  列表、链接、图片与附件（双向同步），以及未知宏（保留并原样回写）
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

## 打包产物（Windows / macOS）

仓库已提供 GitHub Actions 工作流：`.github/workflows/build-packages.yml`。

- 支持平台：`windows-latest`、`macos-latest`
- 触发方式：
  - 手动触发（`workflow_dispatch`）
  - 推送版本标签（`v*`）时自动触发
- 产物形式：
  - Windows：`confluence-markdown-mcp-windows-x64.zip`（内含 `confluence-markdown-mcp.exe`）
  - macOS：`confluence-markdown-mcp-macos.tar.gz`（内含 `confluence-markdown-mcp`）
- 上传位置：
  - 所有构建都会上传到 workflow run 的 Artifacts
  - 当推送 `v*` 标签时，会自动创建/更新同名 GitHub Release 并附加上述打包文件

## 配置（环境变量）

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `CONFLUENCE_BASE_URL` | ✅ | Wiki 根 URL，例如 `https://<your-domain>.atlassian.net` |
| `CONFLUENCE_EMAIL` | 条件必填 | 基于 Basic Auth 登录时的账号邮箱 |
| `CONFLUENCE_API_TOKEN` | 条件必填 | 基于 Basic Auth 登录时的 [API token](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `CONFLUENCE_PAT` | 条件必填 | Personal Access Token；设置后会走 `Authorization: Bearer <PAT>`，且优先于邮箱 + API token |
| `CONFLUENCE_TIMEOUT` | ❎ | HTTP 超时秒数，默认 `30` |
| `CONFLUENCE_MARKDOWN_DIR` | ❎ | `pull` 时相对路径所依赖的默认目录 |
| `CONFLUENCE_IS_CLOUD` | ❎ | 是否为 Confluence Cloud；默认 `true`。设为 `false` 时走 Server/Data Center 的 `/rest/api`；Cloud 走 `/wiki/rest/api` |

认证方式二选一：

- **Basic Auth**：设置 `CONFLUENCE_EMAIL` + `CONFLUENCE_API_TOKEN`
- **PAT**：设置 `CONFLUENCE_PAT`

也就是说，只需要满足上述其中一种组合；并不是这三个认证变量都要同时设置。

### macOS / Linux（bash / zsh）

```bash
export CONFLUENCE_BASE_URL="https://example.atlassian.net"
export CONFLUENCE_EMAIL="you@example.com"
export CONFLUENCE_API_TOKEN="xxxxxxxxxxxx"
```

如使用 PAT：

```bash
export CONFLUENCE_BASE_URL="https://wiki.example.com"
export CONFLUENCE_PAT="xxxxxxxxxxxx"
```

如需永久生效，可将对应认证方式的命令追加到 `~/.bashrc`、`~/.zshrc` 或 `~/.profile` 中，再执行 `source ~/.bashrc`（或对应文件）使其立即生效。

### Windows（命令提示符 CMD）

```cmd
set CONFLUENCE_BASE_URL=https://example.atlassian.net
set CONFLUENCE_EMAIL=you@example.com
set CONFLUENCE_API_TOKEN=xxxxxxxxxxxx
```

如需永久生效，改用 `setx`（注意 `setx` 设置的变量需要重新打开终端才能读取）：

```cmd
setx CONFLUENCE_BASE_URL "https://example.atlassian.net"
setx CONFLUENCE_EMAIL "you@example.com"
setx CONFLUENCE_API_TOKEN "xxxxxxxxxxxx"
```

或者在「控制面板 → 系统 → 高级系统设置 → 环境变量」界面添加用户变量。

### Windows（PowerShell）

```powershell
$env:CONFLUENCE_BASE_URL  = "https://example.atlassian.net"
$env:CONFLUENCE_EMAIL     = "you@example.com"
$env:CONFLUENCE_API_TOKEN = "xxxxxxxxxxxx"
```

如需在 PowerShell 会话间持久化，将以上三行追加到 PowerShell 配置文件（`$PROFILE`）中：

```powershell
Add-Content $PROFILE "`n`$env:CONFLUENCE_BASE_URL  = `"https://example.atlassian.net`""
Add-Content $PROFILE "`$env:CONFLUENCE_EMAIL     = `"you@example.com`""
Add-Content $PROFILE "`$env:CONFLUENCE_API_TOKEN = `"xxxxxxxxxxxx`""
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

# 启动 MCP stdio 服务（默认）
confluence-markdown-mcp serve

# 启动 MCP HTTP 服务（streamable-http，监听 0.0.0.0:8000/mcp）
confluence-markdown-mcp serve --transport streamable-http --host 0.0.0.0 --port 8000

# 启动 MCP SSE 服务（监听 0.0.0.0:8000/sse）
confluence-markdown-mcp serve --transport sse --host 0.0.0.0 --port 8000
```

### HTTP 传输与容器化

`serve` 子命令支持三种 MCP 传输协议：

| `--transport` | 说明 | 默认地址 |
| --- | --- | --- |
| `stdio` | 标准输入输出，适合 Claude Desktop 等本地客户端（默认） | — |
| `sse` | Server-Sent Events，HTTP 长连接 | `http://127.0.0.1:8000/sse` |
| `streamable-http` | 推荐的远程传输，单个 HTTP 端点同时承载请求与流式响应 | `http://127.0.0.1:8000/mcp` |

常用参数：

- `--host` / `--port`：HTTP 监听地址与端口（容器内通常使用 `--host 0.0.0.0`）。
- `--mount-path`：HTTP 应用挂载前缀，默认 `/`。
- `--sse-path` / `--streamable-http-path`：对应传输的 URL 路径。
- `--json-response`：streamable-http 以 JSON 响应代替 SSE 流。
- `--stateless-http`：streamable-http 无状态模式，便于多副本 / 负载均衡部署。

#### 使用 Docker 运行

仓库提供了 `Dockerfile`，默认以 `streamable-http` 传输监听 `0.0.0.0:8000/mcp`。

```bash
# 构建镜像
docker build -t confluence-markdown-mcp .

# 以 streamable-http 模式启动（默认 CMD）
docker run --rm -p 8000:8000 \
  -e CONFLUENCE_BASE_URL="https://example.atlassian.net" \
  -e CONFLUENCE_EMAIL="you@example.com" \
  -e CONFLUENCE_API_TOKEN="xxxxxxxxxxxx" \
  confluence-markdown-mcp

# 自定义参数（例如改成 SSE 传输）
docker run --rm -p 8000:8000 \
  -e CONFLUENCE_BASE_URL="https://example.atlassian.net" \
  -e CONFLUENCE_PAT="xxxxxxxxxxxx" \
  confluence-markdown-mcp \
  serve --transport sse --host 0.0.0.0 --port 8000

# 挂载本地目录供 pull/push 子命令读写 Markdown
docker run --rm -it -v "$PWD/docs:/data" \
  -e CONFLUENCE_BASE_URL="https://example.atlassian.net" \
  -e CONFLUENCE_PAT="xxxxxxxxxxxx" \
  confluence-markdown-mcp \
  pull --page-id 123456 -o /data/
```

客户端通过 HTTP 连接 MCP 时，请将 endpoint 指向 `http://<host>:<port>/mcp`
（streamable-http）或 `http://<host>:<port>/sse`（SSE）。

### `push` 时的附件上传规则

`push_page` / `confluence-markdown-mcp push` 不会仅因为文件位于 Markdown 同级的
`attachments/` 目录中就自动上传所有普通附件。

只有以下两类本地引用会在更新正文前创建 / 更新 Confluence 附件：

1. 图片引用（自动上传）：
   `![image](attachments/example.png)`
2. 带 marker 的普通文件链接：
   `[file](attachments/example.eml) <!--cm-attachment-->`

注意：

- `<!--cm-attachment-->` **必须写在链接后面**，写在前面不会被识别。
- 未加 marker 的普通链接会按普通 `<a href="...">` 处理，不会自动上传为附件。
- URL 编码路径会先 decode 再处理，因此
  `attachments/%E9%99%84%E4%BB%B6%E7%A4%BA%E4%BE%8B.eml`
  会与本地文件 `attachments/附件示例.eml` 匹配，并使用同一个附件文件名。

正确示例：

```md
[附件示例.eml](attachments/附件示例.eml) <!--cm-attachment-->
```

错误示例（marker 在前，不会被识别为附件上传）：

```md
<!--cm-attachment-->[附件示例.eml](attachments/附件示例.eml)
```

## 作为 MCP 服务使用

启动：`confluence-markdown-mcp serve`（stdio 传输，HTTP/容器模式见上文）。

为了让远程 / 容器化部署也安全好用，并且**避免把整篇页面正文直接喂进大模型上下文**，MCP 工具以"文件流"（MCP `EmbeddedResource` 二进制 blob，base64 编码）作为 IO 单位：

| 工具 | 入参 | 返回 |
| --- | --- | --- |
| `pull_page` | `page_id`, `download_attachments?`（默认 true） | 一个 `text` 元数据块（JSON：`page_id` / `title` / `space_key` / `version` / `markdown_resource_uri` / `markdown_bytes` / `attachments[]`）+ 一个 `text/markdown` 的 `EmbeddedResource` blob 作为正文 + 每个附件各一个 `EmbeddedResource` blob |
| `push_page` | `markdown_base64`（必填，由调用方读取本地文件并 base64 编码）；可选 `page_id` / `title` / `upload_attachments` / `attachments=[{filename, content_base64}, ...]` | JSON：`page_id` / `title` / `version` / `attachments[]` |
| `read_page` | `page_id` | 与 `pull_page` 相同的返回结构，但永远不下载附件 |

资源：`confluence://page/{page_id}` — 只读 Markdown 视图，由 host 显式拉取。

要点：

- **服务端不读写调用方的本地文件系统**：`pull_page` 不再接受 `output_dir`，`push_page` 不再接受 `file_path`。所有文件 IO 都由 MCP host（Claude Desktop / Cursor 等）在客户端侧完成——这是 MCP host 原生职责。
- **正文不进入模型上下文**：`pull_page` 返回的 metadata 文本块**只包含元数据，不包含 Markdown 内容**；正文以 `EmbeddedResource` 形式承载，MCP host 通常将其作为可下载文件呈现，模型默认不会读取。如果模型确实需要正文，host 可显式把 blob 作为上下文喂入。
- **多副本 / 多租户安全**：`push_page` 的 `attachments` 文件名会做路径越权检查（拒绝 `..`、`/`、`\` 等）。Markdown 与附件落到服务端的临时目录，每次调用结束立刻删除。
- **本地 CLI 不受影响**：`confluence-markdown-mcp pull/push` 命令行仍然按本地文件路径工作，便于脚本和 CI 使用。

> 旧版本中 `pull_page` 的 `output_dir`、`push_page` 的 `file_path` 入参已移除——它们在 HTTP / 容器部署下本来就是断的（指向的是容器内路径而不是调用方的机器）。如需让容器持续读写一份本地 Markdown 仓库，请改用 CLI 子命令并通过 `docker run -v` 挂载工作目录。

### Claude Desktop

配置文件位置：
- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows：`%APPDATA%\Claude\claude_desktop_config.json`

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

### Cursor

全局配置文件：`~/.cursor/mcp.json`；也可在项目根目录创建 `.cursor/mcp.json` 仅对当前项目生效。

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

### Windsurf

配置文件位置：`~/.codeium/windsurf/mcp_config.json`

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

### VS Code（GitHub Copilot Agent 模式）

在项目根目录创建 `.vscode/mcp.json`（仅对当前工作区生效），或在用户 `settings.json` 中添加 `mcp.servers` 键（全局生效）。

`.vscode/mcp.json`：

```json
{
  "servers": {
    "confluence-markdown": {
      "type": "stdio",
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

### Continue（VS Code / JetBrains 插件）

在 `.continue/config.yaml`（或 `config.json`）的 `mcpServers` 数组中添加：

```yaml
mcpServers:
  - name: confluence-markdown
    command: confluence-markdown-mcp
    args:
      - serve
    env:
      CONFLUENCE_BASE_URL: https://example.atlassian.net
      CONFLUENCE_EMAIL: you@example.com
      CONFLUENCE_API_TOKEN: xxxxxxxxxxxx
```

### 通用备选方案（未全局安装时）

如果尚未将包安装为全局命令，可以改用 `python -m` 方式（将 `python` 替换为实际可执行文件名，例如 `python3`）：

```json
{
  "command": "python",
  "args": ["-m", "confluence_markdown_mcp", "serve"],
  "env": {
    "CONFLUENCE_BASE_URL": "https://example.atlassian.net",
    "CONFLUENCE_EMAIL": "you@example.com",
    "CONFLUENCE_API_TOKEN": "xxxxxxxxxxxx"
  }
}
```

PAT 配置示例：

```json
{
  "command": "confluence-markdown-mcp",
  "args": ["serve"],
  "env": {
    "CONFLUENCE_BASE_URL": "https://wiki.example.com",
    "CONFLUENCE_PAT": "xxxxxxxxxxxx"
  }
}
```

或使用 `uvx` 直接运行（无需手动安装）：

```json
{
  "command": "uvx",
  "args": ["confluence-markdown-mcp", "serve"],
  "env": {
    "CONFLUENCE_BASE_URL": "https://example.atlassian.net",
    "CONFLUENCE_EMAIL": "you@example.com",
    "CONFLUENCE_API_TOKEN": "xxxxxxxxxxxx"
  }
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
| `<p style="text-align: left/right/center/justify">` | 原样保留对齐样式的 `<p>` |
| 行内 `<u>`、`<s>`/`<del>`、`<ins>`、`<sub>`、`<sup>`、`<br>` | 原样保留相同标签 |
| `<a href=...>` / `<img>` | `[text](url)` / `![alt](src)` |
| `plantuml` / `puml` 代码块 | 上传时不使用 wiki 不支持的 `plantuml` 结构宏，而是生成指向 PlantUML SVG 服务的 `<iframe>`，并用 `html-bobswift` 宏包裹；拉取自身生成的 iframe 时还原为 `plantuml` 代码块 |
| `mermaid` 代码块 | 上传时包装成 Confluence `markdown` 结构宏（CDATA 中保留完整 ` ```mermaid ` 围栏），由 wiki 的 Markdown 宏渲染图表；拉取 `markdown` 宏时直接将其 `<ac:plain-text-body>` 当作 Markdown 内容输出，从而原样还原 `mermaid` 代码块 |
| `html` / `html-bobswift` 宏内的 `<iframe>`（drawio / diagrams.net 等） | 拉取时解包为单行 `<iframe …></iframe>`；上传时自动重新用 `html-bobswift` 宏包裹，`src` 限制为 http/https，并剥离 `onload` / `srcdoc` / `sandbox` 等非白名单属性 |
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
