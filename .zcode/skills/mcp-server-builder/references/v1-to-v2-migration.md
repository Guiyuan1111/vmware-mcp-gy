# v1 → v2 迁移对照 —— 看到旧教程时的翻译手册

> 2026 年网上大量教程仍是 v1 写法。本文把旧写法翻译成 v2，用于：读懂用户既有 v1 代码、判断"这个教程能不能照抄"、执行用户点名的迁移。
> 来源：[Python 迁移指南](https://py.sdk.modelcontextprotocol.io/migration/) · [TS 升级指南](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/migration/upgrade-to-v2.md)

## 0. 迁移决策（先问要不要迁）

- **维护既有 v1 项目**：v1 线仍在接收安全修复（TS 承诺 v2 发布后 ≥6 个月，Python v1.x 分支同政策）。没有硬需求就**不迁**，只做安全更新。
- **新项目**：一律 v2（`pip install mcp` 与 `npm i @modelcontextprotocol/server` 现在装的都是 v2 线）。
- v1 代码里识别 v1 的标志：`FastMCP`（Python）、`@modelcontextprotocol/sdk`（TS）、`server.tool()/resource()/prompt()` 链式注册、`StreamableHTTPServerTransport` 接线。

## 1. Python：v1 → v2 对照表

| v1（`mcp` 1.x） | v2（`mcp` 2.x） |
|---|---|
| `from mcp.server.fastmcp import FastMCP` | `from mcp.server import MCPServer` |
| `FastMCP("name")` | `MCPServer("name")`（默认名改为 `mcp-server`） |
| `mcp.run(transport="...")` | 同左，但 **transport 参数只能给 `run()`**，不能给构造函数 |
| `from mcp.server.fastmcp.exceptions import ToolError`（或 `mcp.exceptions`） | `from mcp.server.mcpserver.exceptions import ToolError, ResourceError, ResourceNotFoundError` |
| `from mcp.exceptions import McpError` | `from mcp import MCPError`（改名 + 移位） |
| `mcp.types` | 类型移到独立包 **`mcp-types`**（`import mcp_types`；常用常量如 `INVALID_PARAMS` 仍可从兼容层取） |
| 类型字段 camelCase（如 `CallToolResult` 内部） | 全面改 **snake_case** |
| `AnyUrl` | 资源 URI 类型改为 `str` |
| `MCP_*` 环境变量 / `.env` 自动加载 | **已移除**，显式 `os.environ` |
| `streamablehttp_client` | `Client("http://...")`（URL 即 Streamable HTTP） |
| 请求超时 `timedelta` | 浮点秒数 |
| 客户端请求超时 → HTTP 408 | → `-32001 REQUEST_TIMEOUT` |

v2 行为变化要点（改测试和部署时注意）：

- 同步 handler 在 worker 线程跑（不再阻塞事件循环）。
- `call_tool()` 返回 `CallToolResult`（`.content` / `.structured_content` / `.is_error`）。
- handler 抛 `MCPError` 现在作为 JSON-RPC 错误原样透传（v1 部分场景被包装）。
- Streamable HTTP 请求体默认上限 4 MiB（超限 413）。
- `mcp dev` / `mcp run` 把运行环境锁定为**你装的 SDK 版本**。
- 低层 `Server` 的装饰器 handler 改为构造函数 `on_*` 参数、自动返回值包装移除 —— 迁移低层代码时读官方迁移指南对应小节。

## 2. TypeScript：v1 → v2 对照表

| v1（`@modelcontextprotocol/sdk` 1.x） | v2（`@modelcontextprotocol/server` 2.x） |
|---|---|
| `npm i @modelcontextprotocol/sdk` | `npm i @modelcontextprotocol/server`（客户端另装 `@modelcontextprotocol/client`） |
| `import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"` | `import { McpServer } from '@modelcontextprotocol/server'` |
| `server.tool("name", {schema}, fn)` / `.registerTool` 变体 | `server.registerTool("name", {description, inputSchema}, fn)` |
| `server.resource("name", "uri://x", fn)` | `server.registerResource("name", "uri://x", config, fn)`（config 含 description/mimeType） |
| `server.prompt("name", schema, fn)` | `server.registerPrompt("name", {argsSchema}, fn)` |
| `new StdioServerTransport()` + `server.connect(t)` | `serveStdio(createServer)`（工厂模式；低层 connect 仍在） |
| `StreamableHTTPServerTransport` 逐请求实例 + `app.post` 接线 | `createMcpHandler(factory)` → `handler.fetch` / `toNodeHandler` |
| `McpError` / `ErrorCode` | `ProtocolError` / `ProtocolErrorCode` |
| `import { z } from "zod"`（v3） | `import * as z from 'zod/v4'`（Standard Schema；Valibot/ArkType 亦可） |
| `ReadBuffer`/`getNodeCameraClient` 等杂项 | 拆分进各子包或移除 |

官方 codemod：`npx @modelcontextprotocol/codemod <paths...>` 自动改写常见迁移点，跑完人工复查错误处理与 transport 接线。

## 3. 语义不变项（迁移时不用动的）

- 三原语语义、`isError`/协议错误双轨模型、`ToolError` 消息直达模型的设计 —— v1/v2 一致。
- stdio 铁律（stdout = 协议）、结构化输出双通道、annotations 语义 —— 一致。
- Inspector 工作流 —— 一致（`npx @modelcontextprotocol/inspector <cmd>`）。

## 4. 帮用户迁移的流程

1. `Grep` 全仓定位 v1 标志（`FastMCP`、`@modelcontextprotocol/sdk`、`StreamableHTTPServerTransport`、`server.tool(`）。
2. 向用户报告涉及面（文件数、API 点数）并确认迁移范围；**v1→v2 是破坏性升级，必须用户明确同意**。
3. TS 跑 codemod；Python 按上表逐点改（重点：import 行、异常导入路径、构造函数/run() 参数归位）。
4. 跑测试（内存 Client）；修错误处理断言（snake_case 字段名）。
5. 依赖版本策略：Python `mcp>=2,<3`；TS `@modelcontextprotocol/server@^2`。
6. 全量回归（冒烟 + Inspector + 自动化）后交付，报告中列出所有改动点。
