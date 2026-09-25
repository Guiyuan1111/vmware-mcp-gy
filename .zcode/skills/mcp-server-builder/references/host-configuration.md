# 宿主注册与部署配置大全

> 核验于 2026-09-25。宿主（Host）= 内置 MCP 客户端的应用。注册本质只有一件事：**把服务器的启动命令交给宿主**。
> 来源：[TS SDK real-host 教程](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/get-started/real-host.md) · [Python SDK real-host](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/get-started/real-host.md) · [Claude Code MCP 文档](https://code.claude.com/docs/en/mcp)

## 0. 通用前提

- 一切**路径用绝对路径**（服务器脚本路径、`uv`/`npx` 可执行文件路径）。宿主以自己的工作目录、接近空的环境变量启动子进程 —— 相对路径是最常见失败原因。
- Windows 下 `uv`/`npx` 可能不在宿主的 PATH 中：用 `where uv` / `where npx`（macOS/Linux 用 `which`）取绝对路径写进 `command` 字段。
- 写配置前先向用户展示将要写入的内容并确认；改完配置**完全退出并重启宿主**（Claude Desktop 关窗口 ≠ 退出）。
- 注册前先手动跑通启动命令（见 SKILL.md Step 4a）—— 隔着宿主只能猜错误。

## 1. Claude Code

无需编辑文件，CLI 一条命令注册（`--` 之后全部是启动命令）：

```bash
# TypeScript 服务器
claude mcp add my-server -- npx tsx /abs/path/to/mcp-servers/my-server/src/index.ts

# Python 服务器
claude mcp add bookshop -- uv run --with "mcp[cli]" mcp run /abs/path/to/server.py
```

验证：Claude Code 会话内输入 `/mcp`，应看到服务器与工具列表。

带环境变量（密钥）注册：

```bash
claude mcp add github-issues --env GITHUB_TOKEN=xxx -- npx tsx /abs/path/src/index.ts
```

作用域说明：默认 local（仅当前项目当前用户）；`-s project` 写入项目 `.mcp.json` 可提交共享；`-s user` 对该用户所有项目生效。团队共享时优先 `project` 作用域，密钥仍走各人 env。

## 2. Claude Desktop

**方式 A（Python 专用，推荐）**：SDK CLI 自动写入：

```bash
uv run mcp install server.py
uv run mcp install server.py --name "Bookshop" -v API_KEY=abc123 -f .env
```

它写入的条目（自动取 uv 绝对路径 + 锁定 SDK 版本 + 绝对脚本路径）：

```json
{
  "mcpServers": {
    "Bookshop": {
      "command": "/absolute/path/to/uv",
      "args": ["run", "--frozen", "--with", "mcp[cli]==2.2.0", "mcp", "run", "/absolute/path/to/server.py"]
    }
  }
}
```

**方式 B（手写）**：编辑 `claude_desktop_config.json`：

- **macOS**：`~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**：`%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["/abs/path/to/mcp-servers/my-server/dist/index.js"],
      "env": { "API_KEY": "user-provided" }
    }
  }
}
```

注意：

- `mcp install` 报 `Claude app not found` = 配置目录还不存在，先安装并运行一次 Claude Desktop。
- 修改后完全退出（托盘图标也退出）再重开。
- 服务器日志：macOS `~/Library/Logs/Claude/mcp-server-<NAME>.log`；Windows `%APPDATA%\Claude\logs\`。
- Claude Desktop 只支持 stdio 服务器（它启动本地子进程）；远程服务器用 Claude Code / 其他支持 HTTP 的客户端。

## 3. VS Code（GitHub Copilot）

项目根 `.vscode/mcp.json` —— **顶层键是 `servers`**，每项带 `type`：

```json
{
  "servers": {
    "weather": {
      "type": "stdio",
      "command": "npx",
      "args": ["tsx", "${workspaceFolder}/src/index.ts"]
    }
  }
}
```

- 需要 VS Code ≥ 1.99 + GitHub Copilot 扩展已登录（Copilot Free 即可）。
- 首次连接会弹信任提示，确认之；Command Palette 执行 **MCP: List Servers** 查看运行状态。
- 工具不调用时：Copilot Chat 必须处于 **Agent 模式**（其他模式不调用工具），再执行 **MCP: Reset Cached Tools**。
- 远程 HTTP 服务器：`"type": "http"`，`"url": "http://..."`。

## 4. Cursor

项目根 `.cursor/mcp.json` —— **顶层键是 `mcpServers`**（与 Claude Desktop 同风格，无 `type`）：

```json
{
  "mcpServers": {
    "weather": {
      "command": "npx",
      "args": ["tsx", "/abs/path/to/src/index.ts"]
    }
  }
}
```

远程：`"url": "http://..."`（SSE/HTTP 类型）。设置 → MCP 页查看状态。

## 5. 四家配置速查表

| 宿主 | 文件 | 顶层键 | type 字段 | 注册方式 |
|------|------|--------|-----------|---------|
| Claude Code | 免文件（CLI） | — | — | `claude mcp add name -- cmd...` |
| Claude Desktop | `claude_desktop_config.json` | `mcpServers` | 无 | `mcp install` 或手写 |
| VS Code | `.vscode/mcp.json` | `servers` | **必须**（stdio/http） | 手写 |
| Cursor | `.cursor/mcp.json` | `mcpServers` | 无（URL 即远程） | 手写 |

条目核心永远是同一对 `command` + `args`（stdio）或 `url`（远程），差别只在文件位置与包装键。

## 6. 远程部署（Streamable HTTP）清单

供多客户端通过 URL 接入时：

- **Host/Origin 校验**（防 DNS 重绑定）：TS 用框架工厂（express/fastify/hono 默认开）或 `localhostHostValidation`/`localhostOriginValidation`；Python 侧按 [Deploy & scale](https://py.sdk.modelcontextprotocol.io/run/deploy/) 配 `transport_security`。公网部署绑定 `0.0.0.0` 时此项不可省。
- **鉴权**：公网必须加（OAuth 2.1 或至少 bearer token 中间件，TS `requireBearerAuth`）；`authInfo` 透传进工厂按调用者区分数据。
- **无状态**：TS `createMcpHandler` 天然每请求新实例；Python 加 `stateless_http=True`。跨请求状态用服务器自签句柄（工具参数携带），不依赖连接内存。
- **反向代理**：走 nginx/Caddy 时注意请求体上限（Python 默认 4 MiB）与 SSE 流的缓冲关闭。
- 交付 URL 形如 `https://host/mcp`（Python 默认端点路径 `/mcp`）。

## 7. 发布分发

- **npm（TS）**：`package.json` 声明 `"bin": {"my-server": "./dist/index.js"}` + 构建；用户 `npx my-server` 即用。发布前征得用户同意。
- **PyPI（Python）**：pyproject 配置 console script；用户 `uvx my-server` 即用。
- 服务器本体 README 需含：功能简介、启动命令、必需环境变量表、宿主配置 JSON 片段（直接可复制）。
- 可选择提交到官方 Registry（https://github.com/modelcontextprotocol/registry ）供公网发现。
