---
name: mcp-server-builder
description: >-
  Build production-ready Model Context Protocol (MCP) servers end to end:
  scaffold the project, implement tools/resources/prompts with the official
  SDKs (Python mcp v2 / TypeScript @modelcontextprotocol/server v2), test with
  MCP Inspector and in-memory clients, register with hosts (Claude Code,
  Claude Desktop, VS Code, Cursor), and debug stdio/Streamable HTTP issues.
  Triggered by: "写MCP", "开发MCP", "MCP server", "MCP服务器", "MCP工具",
  "创建MCP", "mcp builder", "MCP集成", "Model Context Protocol", "写一个MCP".
version: 1.0.0
allowed-tools: Read Write Edit Glob Grep Bash WebSearch WebFetch
metadata:
  tags: mcp, model-context-protocol, server-development, llm-integration, tools
argument-hint: "[要实现的功能描述，可含目标语言/要封装的API]"
---

# MCP Server Builder — 构建 Model Context Protocol 服务器

## Purpose

根据用户的功能需求，从零构建（或扩展）一个**可直接运行、可接入真实宿主**的 MCP（Model Context Protocol）服务器：完成技术选型 → 脚手架 → 实现 tools/resources/prompts → 测试 → 宿主注册 → 分发交付的全流程。功能正确可运行是第一目标，所有代码均使用官方 SDK 当前稳定版（v2 线）的 API，不使用过时写法。

## When to Use

- 用户要求"写一个 MCP server / MCP 服务器"，把某个能力（查数据库、调 API、读文件、发消息等）暴露给 Claude 或其他 LLM 客户端
- 用户要求"给 XX 做 MCP 集成"、"封装 XX API 成 MCP 工具"
- 用户要求为**已有的 MCP 服务器添加新工具/资源/提示**（扩展路径，见 Step 3 的规则）
- 用户抱怨自己写的 MCP server 连不上、工具不显示、调用报错（先走 Step 6 排错）
- 用户要求把现有 MCP 服务器从 stdio 改造成 Streamable HTTP 部署，或做宿主注册

## When NOT to Use

- 用户想调用**别人已发布的** MCP 服务器（安装配置类问题）—— 直接帮其写宿主配置即可，无需本技能的开发流程
- 用户想构建 **MCP 客户端（Client）或宿主应用** —— 本技能只覆盖服务器端；客户端开发参考 [Python SDK 文档](https://py.sdk.modelcontextprotocol.io/client/) 与 [TS SDK 文档](https://ts.sdk.modelcontextprotocol.io/v2/)
- 用户想要的是普通 CLI 工具、REST API 或函数库，明确不需要 MCP 协议
- 用户询问 MCP 概念/规范本身而不要求写代码 —— 直接用 `references/spec-notes.md` 中的内容作答

## 已核实事实速查（2026-09-25 核验）

写任何代码之前先掌握这些**已核实**的事实，禁止凭旧记忆使用过时 API：

| 事实 | 当前状态 | 来源 |
|------|---------|------|
| MCP 规范最新版本 | **2026-07-28**（历史：2024-11-05 → 2025-03-26 → 2025-06-18 → 2025-11-25） | [规范版本页](https://modelcontextprotocol.io/specification/2026-07-28) |
| 2026-07-28 规范要点 | MCP 改为**无状态**：移除 initialize 握手与 `Mcp-Session-Id`；每请求在 `_meta` 携带协议版本与客户端能力；新增 `server/discover`；`subscriptions/listen` 取代 `resources/subscribe`；资源未找到返回 `-32602` | [官方 Changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog) |
| Python 官方 SDK | `mcp` **2.2.0**（v2 稳定线，要求 Python ≥ 3.10）。核心类为 **`MCPServer`**（v1 的 `FastMCP` 已改名）。安装：`uv add "mcp[cli]"` 或 `pip install "mcp[cli]"` | [PyPI](https://pypi.org/project/mcp/)、[Python SDK 文档](https://py.sdk.modelcontextprotocol.io/) |
| TypeScript 官方 SDK | v2 拆分为 **`@modelcontextprotocol/server`** 2.1.0（建服务器）与 **`@modelcontextprotocol/client`** 2.1.0。旧单包 `@modelcontextprotocol/sdk`（1.30.1）是 v1 遗留线。要求 Node ≥ 20，**仅 ESM**，schema 用 Zod v4（`zod/v4`，Standard Schema 兼容） | [npm:server](https://www.npmjs.com/package/@modelcontextprotocol/server)、[TS v2 文档](https://ts.sdk.modelcontextprotocol.io/v2/) |
| MCP Inspector | `@modelcontextprotocol/inspector` **2.8.0**，运行：`npx @modelcontextprotocol/inspector <启动命令>` | [Inspector 仓库](https://github.com/modelcontextprotocol/inspector) |
| stdio 铁律 | **stdout 是协议通道**。任何 print/console.log 落到 stdout 都会损坏 JSON-RPC 流导致宿主断连；日志一律走 stderr（Python 用 `logging`，TS 用 `console.error`） | [Python 文档](https://py.sdk.modelcontextprotocol.io/servers/running/)、[TS 教程](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/get-started/first-server.md) |
| SSE 传输 | 已被 Streamable HTTP 取代（2025-03-26 起）。**禁止**在新项目中使用 SSE | [规范 Changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog) |
| 独立 fastmcp 包 | PyPI 上另有第三方框架 `fastmcp`（当前 4.0.9），与官方 SDK 内置的旧 FastMCP **不是一回事**。除非用户点名，否则用官方 SDK | [PyPI: fastmcp](https://pypi.org/project/fastmcp/) |

> 写代码前若距离本技能的核验日期已久，先用 `npm view @modelcontextprotocol/server version` 和 `curl -s https://pypi.org/pypi/mcp/json` 复核版本（见 Step 1）。

## Workflow / Steps

按顺序执行。每一步的产物是下一步的输入；任何一步失败按该步的降级方式处理，禁止静默跳过。

### Step 0: 需求分析与技术决策

从 `$ARGUMENTS` 提取：要封装的能力、数据来源（本地文件/数据库/远程 API）、目标宿主、用户点名的语言或框架。缺关键信息时先向用户确认，不猜测。

做出三个决策并告知用户：

**0a. 语言选择**（默认规则，用户点名则从其指定）：

| 场景 | 选择 |
|------|------|
| 数据处理、数据库、AI/科学计算、用户熟悉 Python | Python（`mcp` SDK v2） |
| Web/前端生态、Node 集成、需要部署到边缘运行时 | TypeScript（`@modelcontextprotocol/server` v2） |
| 未指定 | 按用户项目现有技术栈；两者皆无 → Python |

**0b. 传输（transport）选择**：

| 场景 | 传输 |
|------|------|
| 本机个人使用，由 Claude Desktop / Claude Code / VS Code / Cursor 作为子进程启动 | **stdio**（默认，零配置） |
| 部署成远程服务，多客户端通过 URL 接入 | **Streamable HTTP**（HTTP 部署时必须做 Host/Origin 校验，见安全约束） |
| 用户要求 SSE | 拒绝并向用户说明 SSE 已废弃，改用 Streamable HTTP |

**0c. 原语（primitive）选择** —— 三个都提供是错的，按语义挑选：

| 原语 | 控制方 | 适用 |
|------|--------|------|
| **Tool** | 模型决定调用 | 有副作用的动作、查询、计算 —— 绝大多数需求 |
| **Resource** | 宿主/应用决定读取 | 只读上下文数据（配置、文档、记录），用 URI 寻址 |
| **Prompt** | 用户主动选择 | 面向用户的消息模板（斜杠命令式） |

输出一份 3-5 行的决策摘要（语言 / 传输 / 原语清单），然后继续。

### Step 1: 环境检查与依赖核实

1. 运行本技能自带的 `scripts/check_env.py --lang python|typescript`（路径以实际安装位置为准，如 `.claude/skills/mcp-server-builder/scripts/check_env.py`；检测 Python/uv/Node/npx 与已装 SDK 版本）。脚本不可用时手动检查：`uv --version`、`node --version`、`npx --version`。
2. 缺失依赖时向用户给出安装命令并征得同意后执行：
   - Python 侧：安装 [uv](https://docs.astral.sh/uv/)（推荐）；SDK 随项目安装，无需全局装。
   - TS 侧：Node.js ≥ 20。
3. 复核 SDK 最新版本（离线环境可跳过，但须在最终报告中注明未核实）：
   ```bash
   npm view @modelcontextprotocol/server version
   curl -s https://pypi.org/pypi/mcp/json | python -c "import sys,json;print(json.load(sys.stdin)['info']['version'])"
   ```
4. 若用户需求涉及第三方 Web API：用 `WebSearch`/`WebFetch` 核实该 API 的当前端点、认证方式与限流策略（至少两个来源交叉验证，官方文档优先），结果记入 `references` 引用备注。

**降级**：环境完全离线 → 基于本技能 references 中已核实的 API 编写，并在报告中声明"未联网复核版本"。

### Step 2: 项目脚手架

**输出路径规则**：默认在当前项目根目录下创建 `./mcp-servers/<server-name>/`；用户明确指定目标目录时从其指定。`<server-name>` 用 kebab-case（2-4 个单词）。

- **Python 路线**：复制 `templates/python/src/server.py` 与 `templates/python/pyproject.toml` 到 `./mcp-servers/<server-name>/`（保持 `src/` 布局或按需拍平，拍平时同步 `pyproject.toml` 无额外配置），然后 `uv sync`（或 `pip install -e .`）。
- **TypeScript 路线**：复制 `templates/typescript/` 下全部文件到 `./mcp-servers/<server-name>/`，然后 `npm install`。

模板已内置：一个示例 tool + resource + prompt、stderr 日志、`ToolError`/`isError` 错误示范、`if __name__ == "__main__"`（Python）或 `serveStdio` 工厂（TS）。复制后**删除示例代码，换成本次需求的真实实现**。

### Step 3: 实现服务器

在脚手架上实现 Step 0c 确定的原语。两条路线的 API 差异大，**写代码前必读对应参考文档**：

- Python：读 `references/python-sdk-v2.md`
- TypeScript：读 `references/typescript-sdk-v2.md`

**3a. 工具设计规范**（详细论述见 `references/tool-design-guide.md`）：

- 每个工具写**面向模型的 description**：说明做什么、何时用、返回什么。description 是模型选工具的唯一依据之一，写得含糊 = 工具等于不存在。
- 输入 schema 的每个字段加 `.describe()`（TS）或 `Field(description=...)`（Python）；必填项最小化，可选参数给默认值。
- 单个工具做一件完整的事；超过约 20 个工具时按域拆分参数或建议拆分服务器。
- 有结构化返回需求（客户端程序要读结果）时用结构化输出：Python 直接写返回类型注解；TS 加 `outputSchema` + `structuredContent`。
- 危险工具（删除、覆盖、发消息、花钱）必须带 annotations 声明行为提示（`readOnlyHint`/`destructiveHint`/`idempotentHint`），并在 description 中写明后果。

**3b. 错误处理（双轨模型，两 SDK 一致的语义）**：

- **模型能自行修复的失败**（查无此记录、参数组合无效、上游暂时超时）→ 工具错误：Python `raise ToolError("...含修复建议...")`；TS 返回 `{ content: [...], isError: true }` 或直接 `throw new Error("...")`。消息里写清**如何修正**，模型读到后会重试。
- **请求本身不合法/模型无法修复**（缺客户端能力、服务状态不可用）→ 协议错误：Python `raise MCPError(code, message)`；TS `throw new ProtocolError(ProtocolErrorCode.InvalidParams, ...)`。
- 判断口诀：**"换个更聪明的模型能不能避免这个失败？" 能 → 工具错误；不能 → 协议错误。**
- 禁止用普通 `return "错误：..."` 报告失败 —— 那会被当成正常结果，模型以为成功了。
- 未捕获异常两侧 SDK 都会变成 `is_error=True` 的通用失败（细节只进服务器日志），所以预期内的失败必须显式抛 `ToolError`/`isError`。
- Python 资源未找到抛 `ResourceNotFoundError`（映射为协议码 -32602 并携带 URI）。

**3c. 外部调用健壮性（API 集成类推断项）**：

- 凭证一律从环境变量读取；禁止硬编码。注册到宿主时把密钥写进宿主配置的 `env` 字段（见 `references/host-configuration.md`），不写进代码或仓库。
- 对远程 API 的调用：设超时（如 30s）、失败重试 ≤ 2 次加指数退避、识别 429 并按 `Retry-After` 等待；重试耗尽后抛 `ToolError` 说明状态。
- 响应解析：校验关键字段存在后再使用；上游返回结构变化时抛带上下文的 `ToolError`，不让 KeyError/undefined 直接漏出。

**3d. 日志纪律**：

- Python：用 `logging` 模块（SDK 默认配置输出到 stderr）；禁止 `print(..., flush=True)` 到 stdout。
- TypeScript：`console.error`；禁止任何 `console.log`/`process.stdout.write`。
- 日志记录：工具名、参数摘要（脱敏）、耗时、错误堆栈。禁止记录密钥与完整用户数据。

**3e. 扩展已有服务器时**：先用 `Read` 通读现有实现，沿用其既有的命名风格、错误处理模式与日志方式；只新增/修改与需求相关的部分，不重构无关代码。

### Step 4: 测试

按顺序执行三层，前两层通过才算实现完成：

**4a. 冒烟启动**（stdio 必做）：手动运行启动命令。
- Python：`uv run mcp run server.py`（或 `python server.py`）—— 正确表现是**无输出且挂起等待 stdin**（Ctrl-C 退出）。出现 traceback 或立即退出即为 bug，就地修复。
- TS：`npx tsx src/index.ts` —— 正确表现是 stderr 出现一行横幅后挂起。

**4b. Inspector 交互测试**：
```bash
# Python
uv run mcp dev server.py
# TypeScript
npx @modelcontextprotocol/inspector npx tsx src/index.ts
```
在打开的浏览器页面里 Connect → Tools 页逐个调用工具：验证正常路径、边界参数（空值、超界）、错误路径（错误消息是否含修复建议）。无法开浏览器时改用 4c 的自动化测试替代本层。

**4c. 自动化测试**（推荐写为项目测试）：
- Python：内存 `Client` 直连服务器对象 —— `Client(mcp, raise_exceptions=True)` + pytest/anyio，断言 `result.content` 与 `result.structured_content`。写法见 `references/python-sdk-v2.md` §测试。
- TS：`createMcpHandler(createServer)` 的 `handler.fetch` 接到 `StreamableHTTPClientTransport` 的 `fetch` 选项，全程不占端口；断言 `structuredContent` 与 `isError`。写法见 `references/typescript-sdk-v2.md` §测试。
- 测试必须覆盖：每个工具的正常路径、每个预期错误路径、schema 拒绝非法参数的路径。

### Step 5: 接入宿主

按用户使用的宿主注册服务器（各宿主的配置文件格式与路径差异见 `references/host-configuration.md`，含完整 JSON 片段）：

| 宿主 | 方式 |
|------|------|
| Claude Code | `claude mcp add <name> -- <启动命令...>`，会话内 `/mcp` 验证 |
| Claude Desktop | Python 服务器可 `uv run mcp install server.py`；或手写 `claude_desktop_config.json`（macOS：`~/Library/Application Support/Claude/`；Windows：`%APPDATA%\Claude\`） |
| VS Code | 项目根 `.vscode/mcp.json`，顶层键为 `servers`，每项带 `"type": "stdio"` |
| Cursor | 项目根 `.cursor/mcp.json`，顶层键为 `mcpServers` |

注册规则：

- **一切路径用绝对路径** —— 宿主从它自己的工作目录启动子进程，相对路径是最常见的失败原因。
- 修改宿主配置后**完全退出并重启宿主**（Claude Desktop 关窗口不算退出）。
- 把启动命令写入宿主配置前先向用户展示将要写入的内容并征得同意（`claude mcp add` 与配置文件都是对用户环境的修改）。
- 涉及密钥的工具：把环境变量写入宿主配置条目的 `env` 字段（如 Claude Desktop 的 `"env": {"API_KEY": "..."}`），值请用户提供，禁止替用户编造。

### Step 6: 排错（宿主连不上 / 工具不显示 / 调用失败时）

1. **先手动跑启动命令**（Step 4a）——这一步能看见真实报错，隔着宿主只能猜。
2. 挂起正常但宿主看不到 → 逐项核对：路径是否绝对？配置文件名与顶层键对不对（`servers` vs `mcpServers`）？宿主重启了吗？
3. 连上但工具不显示 → 检查 description/schema 是否合法；Copilot 需处于 Agent 模式并执行 `MCP: Reset Cached Tools`。
4. 调用报"corrupt message"/连接断开 → 全仓搜 `console.log`、`print(`，消灭一切写 stdout 的代码。
5. 仍无法解决 → 按 `references/troubleshooting.md` 的错误对照表逐条排查。

### Step 7: 分发与交付报告

**7a. 分发（用户需要给别人用时）**：
- 本地分发：交付项目目录 + README（含启动命令与 `env` 要求）。
- Python 可发布到 PyPI 后用 `uvx <包名>` 运行；TS 发布到 npm 后用 `npx <包名>` 运行。发布属于对外动作，必须先征得用户同意。
- 远程部署（Streamable HTTP）：补 Host/Origin 校验与鉴权（`references/host-configuration.md` §远程部署），不在本步默认展开。

**7b. 交付报告**必须包含：

1. 服务器名称、路径、语言与 SDK 版本（写明 Step 1 核实的具体版本号）
2. 工具/资源/提示清单：每个的名称、一句话功能、输入输出概要
3. 测试结论：冒烟/Inspector/自动化三层各自的通过情况
4. 宿主注册方式：写好的配置片段或命令，及需要用户自己填的密钥项
5. 已知限制与后续建议（如限流、缓存、拆分）
6. 信息来源：引用过的官方文档完整链接

## Constraints

**Always**

- Always 使用官方 SDK 当前 v2 线 API：Python `from mcp.server import MCPServer`；TS `@modelcontextprotocol/server` + `zod/v4`。禁止使用 v1 写法（`FastMCP`、`@modelcontextprotocol/sdk`、`server.tool()`、`registerResource` 之外的旧资源 API）——旧教程对照见 `references/v1-to-v2-migration.md`
- Always 把 stdout 当作协议通道保护：Python 用 `logging` 到 stderr，TS 只用 `console.error`；任何情况下不让业务输出污染 stdout
- Always 为每个工具写面向模型的 description，为每个 schema 字段写 describe/Field 说明
- Always 按"模型能否自我修复"的双轨模型抛错误；预期失败禁止用返回值表达
- Always 凭证走环境变量 + 宿主配置 `env` 字段；日志与返回内容中不出现密钥
- Always 在写代码前读对应语言的 references 文档；在交付报告中标注 SDK 版本与来源链接
- Always 对涉及的外部 API 先联网核实（≥2 来源交叉），无法核实则在报告中声明
- Always 执行 Step 9 自检清单后再向用户报告
- Always 新文件写入 `./mcp-servers/<server-name>/`（或用户明确指定的目录）；禁止写入项目根目录或临时目录
- Always 处理文件路径输入时先 resolve + 校验边界（防目录穿越），网络部署时校验 Host/Origin（防 DNS 重绑定）

**Never**

- Never 使用 SSE 传输或建议用户使用
- Never 在 stdio 服务器中向 stdout 打印任何内容
- Never 硬编码 API 密钥、token 或把密钥写进仓库/日志/返回值
- Never 在未征得用户同意的情况下执行 `claude mcp add`、改宿主配置文件、发布 npm/PyPI
- Never 编造未核实的 API 端点、SDK 方法名或版本号；不确定就去查，查不到就明说
- Never 一次注册超过 20 个含义相近的工具；先合并同类，用参数区分
- Never 用 `return` 字符串表达工具失败

## Examples

### ✅ Do This

**输入**：`/mcp-server-builder 写一个查询 GitHub 仓库 issue 的 MCP server，给 Claude Code 用`

**正确做法**：
- Step 0：能力 = 调 GitHub API 查 issue → 原语 = Tool（`list-issues`、`get-issue`）；宿主 = Claude Code → stdio；语言未点名 → 询问或按用户项目栈定，假定 TS。
- Step 1：`check_env.py --lang typescript` 确认 Node ≥ 20；`npm view @modelcontextprotocol/server version` 得到 2.1.0；WebSearch 核实 GitHub REST `/repos/{owner}/{repo}/issues` 的当前认证方式（token 走 `GITHUB_TOKEN` 环境变量）与限流（认证 5000 req/h）。
- Step 2：脚手架到 `./mcp-servers/github-issues/`，`npm install @modelcontextprotocol/server zod tsx`。
- Step 3：实现时 —— description 写明"列出某仓库的开放 issue，返回编号/标题/作者/时间"；`owner`/`repo` 必填并 describe；请求带 `Authorization: Bearer ${process.env.GITHUB_TOKEN}`；429 时读 `Retry-After` 等待重试 1 次；无 token 时降级为匿名限流并在 description 中说明；查询失败 `throw new Error("GitHub API HTTP 404：仓库不存在，请检查 owner/repo 拼写")` —— 模型能自行纠正。
- Step 4：`npx tsx src/index.ts` 冒烟（stderr 一行横幅后挂起）→ Inspector 连接并调用 `list-issues {owner:"facebook", repo:"react"}` → 自动化测试覆盖正常 + 404 + 限流三条路径。
- Step 5：`claude mcp add github-issues -- npx tsx /abs/path/src/index.ts`（先展示给用户确认），提醒用户把 `GITHUB_TOKEN` 写进 Claude Code 的 MCP 配置 env。
- Step 7：报告含工具清单、测试结果、配置片段、来源链接（GitHub REST 文档、TS SDK 文档）。

### ❌ Not This

- 用 `npm install @modelcontextprotocol/sdk`（v1 遗留包）并照旧教程写 `server.tool(...)` —— 生成的是旧线代码，与 v2 文档冲突，无法与 `mcp dev`/新文档对齐
- 在工具 handler 里 `console.log(JSON.stringify(result))` 调试后忘了删 —— stdout 被污染，宿主直接断连，用户看到"服务器连不上"
- 查不到 issue 时 `return "Error: not found"` —— `isError=false`，模型以为查询成功，把错误文案当答案复述给用户
- 把 `GITHUB_TOKEN = "ghp_xxx"` 硬编码进源码并提交
- 用户要 SSE 远程服务器时照做 —— SSE 已废弃，应改用 Streamable HTTP 并说明原因
- 写完只跑通 Inspector 就交付，跳过宿主注册环节 —— 用户拿到的"能用"只是开发视角的能用
- description 写 `查询 issue` 三个字完事 —— 模型缺乏何时用/参数含义/返回内容的信息，工具选择命中率大跌

## Notes

- **v1 兼容**：v1 线（Python `mcp>=1.28,<2` 的 FastMCP、TS `@modelcontextprotocol/sdk` 1.x）仍在接收安全修复；维护用户既有 v1 项目时**不要**强行升 v2，改动前确认用户意愿，迁移对照见 `references/v1-to-v2-migration.md`
- **协议版本**：SDK v2 同时服务旧协议（2025-era）与 2026-07-28 规范（era 概念）；日常开发无需手动处理，做协议级调试时读 `references/spec-notes.md`
- **第三方框架**：`fastmcp`（PyPI 独立包）功能更花哨但属第三方生态；用户点名时按其官方文档（https://github.com/jlowin/fastmcp ）开发，否则默认官方 SDK
- **规模感知**：工具数量增长导致 description 膨胀时，建议按领域拆成多个服务器（一个服务器一个主题），宿主可同时注册多个
- **Windows 注意**：宿主配置中的命令路径用绝对路径且注意 `npx`/`uv` 可能不在宿主的 PATH 中，用 `where uv` / `where npx` 取绝对路径写入
- **本技能信息时效**：核心事实核验于 2026-09-25，全部来源见 `references/spec-notes.md` §来源清单

## 自检清单（报告前逐项确认）

- [ ] 使用的是 v2 SDK API（Python `MCPServer` / TS `@modelcontextprotocol/server`）？
- [ ] stdout 零污染（无 print/console.log 到 stdout）？
- [ ] 每个工具：description + 字段 describe + 错误路径齐全？
- [ ] 预期失败全部走 ToolError/isError，无 return 错误字符串？
- [ ] 凭证全部环境变量化，日志无泄漏？
- [ ] 三层测试（冒烟/Inspector/自动化）执行情况已如实记录？
- [ ] 宿主注册片段使用绝对路径且已向用户展示确认？
- [ ] 交付报告含 SDK 版本号、工具清单、来源链接、已知限制？
