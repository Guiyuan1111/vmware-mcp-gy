# MCP 规范要点与版本演进

> 核验于 2026-09-25。用于回答协议概念问题、做协议级调试、判断"某特性是否可用"。
> 日常业务开发只需掌握 §1 与 §3；做跨版本兼容或协议调试时读全文。

## 1. 规范版本线

| 版本 | 发布 | 关键变化 |
|------|------|---------|
| 2024-11-05 | 2024-11 | 首个正式版：tools/resources/prompts 三原语、stdio + HTTP SSE 传输 |
| 2025-03-26 | 2025-03 | OAuth 2.1 授权框架；Streamable HTTP 取代 HTTP+SSE |
| 2025-06-18 | 2025-06 | 结构化工具输出（structuredContent/outputSchema）；elicitation；MCP 服务器作为 OAuth 资源服务器 |
| 2025-11-25 | 2025-11 | 资源未找到改判 `-32602`（SEP-2164）等 |
| **2026-07-28** | 2026-07 | **无状态化改造**（见 §2），当前最新版 |

来源：[规范首页](https://modelcontextprotocol.io/specification/2026-07-28)、[官方 Changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)

## 2. 2026-07-28 版关键变化（SEP-2567 / SEP-2575）

SDK v2 已把这些封装好，业务代码通常无感知；但排查部署与兼容性问题时需要知道：

- **移除协议级会话**：Streamable HTTP 不再有 `Mcp-Session-Id` 头；`tools/list` 等列表端点不再随连接变化。需要跨调用状态的服务器用**自己签发的句柄**当普通工具参数传递（如返回 `session_id` 让模型下次带上）。
- **移除 initialize 握手**：每个请求在 `_meta` 中自带协议版本（`io.modelcontextprotocol/protocolVersion`）与客户端能力（`io.modelcontextprotocol/clientCapabilities`）；客户端应每请求自报身份（`io.modelcontextprotocol/clientInfo`），服务器在每个结果的 `_meta` 里回 `io.modelcontextprotocol/serverInfo`。
- **新增 `server/discover`**：服务器必须实现，用于宣告支持的协议版本、能力与身份；客户端可在任何请求前调用它做版本选择或 stdio 兼容探测。
- **`subscriptions/listen`** 取代 HTTP GET 长流与 `resources/subscribe`/`unsubscribe`：单条长连接 POST 流，客户端按类型（`toolsListChanged`、`resourceSubscriptions` 等）选择订阅。
- **移除 `ping`、`logging/setLevel`、`notifications/roots/list_changed`**：日志级别改为每请求经 `_meta` 的 `io.modelcontextprotocol/logLevel` 携带。
- **新错误码**：`-32021`（缺客户端能力）、`-32022`（协议版本不支持）；资源未找到为 `-32602`（自 2025-11-25 起）。

对开发姿势的实际影响：**把 handler 写成无状态纯函数**（请求间不依赖连接级内存状态），既天然兼容新规范，也是 HTTP 水平扩展的前提。TS v2 的 `createMcpHandler` 工厂模式、Python v2 的 `stateless_http=True` 都服务于此。

## 3. 三原语语义（选型依据）

| | Tool | Resource | Prompt |
|---|------|----------|--------|
| 谁控制 | **模型**决定何时调用 | **宿主应用**决定何时读取 | **用户**主动选择 |
| 方向 | 动作/查询（可有副作用） | 只读上下文数据 | 消息模板注入对话 |
| 寻址 | 按名称 + JSON Schema | 按 URI（可模板化） | 按名称 + 扁平字符串参数 |
| 典型例 | 查订单、发消息、跑分析 | 配置文件、日志、文档片段 | "code review"、"写周报" |

经验法则：拿不准就做成 Tool；确有"挂上下文给应用按需取"的只读数据才做 Resource；真正面向用户手动触发的模板才做 Prompt。

## 4. 传输（Transport）

| 传输 | 状态 | 适用 |
|------|------|------|
| **stdio** | ✅ 现行 | 宿主把服务器作为子进程启动，stdin/stdout 通信。本地集成默认选择 |
| **Streamable HTTP** | ✅ 现行 | HTTP POST + 按需 SSE 升级流。一切远程/多客户端部署 |
| HTTP + SSE（旧） | ❌ 2025-03-26 起废弃 | 仅为未迁移旧客户端保留，新代码禁用 |

stdio 核心纪律：**stdout = 协议通道**。Python 侧 SDK 会把"运行期间 flush 到 stdout 的杂散输出"改道 stderr，但导入期 print、包装脚本 echo、退出时才 drain 的缓冲 print 仍会污染流 —— 从源头上不写。

## 5. 安全模型要点

- **授权**：HTTP 部署用 OAuth 2.1（2025-06-18 起，服务器作为资源服务器）；stdio 服务器继承启动它的宿主进程的信任域，不要自行做网络鉴权。
- **DNS 重绑定防护**：本机 HTTP 服务必须校验 `Host`/`Origin`（TS 的框架工厂默认开；裸 node:http 用 `localhostHostValidation`/`localhostOriginValidation`；Python 看 Deploy 文档的 `transport_security`）。
- **混淆代理问题**：服务器持有静态凭证（如长期 token）替用户调第三方 API 时，注意不要把凭证作用扩大到"以用户身份"的操作上；能走 OAuth 委托就走委托。
- **工具描述注入**：工具返回的内容会进入模型上下文。对外部数据（网页、仓库 issue 正文等）保持警惕，服务器不要把外部文本包装成指令式措辞返回。

来源：[规范 Authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)、[Security Best Practices](https://modelcontextprotocol.io/specification/2026-07-28/basic/security_best_practices)

## 6. 生态术语表

| 术语 | 含义 |
|------|------|
| **Host** | 带模型的终端应用（Claude Desktop、Claude Code、VS Code、Cursor），内含 MCP Client |
| **Client** | 宿主内与服务器通信的连接器，1:1 连接一个服务器 |
| **Server** | 通过原语暴露能力的进程/服务（本技能的产出物） |
| **Era** | TS/Python v2 SDK 中的概念：一条连接按其说的协议版本分 "2025-era（legacy，带握手）" 与 "2026-07-28（modern，无状态）" |
| **SEP** | MCP Standards Enhancement Proposal（规范增强提案），如 SEP-2164（资源错误码）、SEP-2567（无状态句柄）、SEP-2575（无状态握手移除） |
| **Inspector** | 官方交互式调试器（浏览器 UI），直接启动并调用你的服务器 |
| **Registry** | 官方 MCP 服务器注册表（https://github.com/modelcontextprotocol/registry ），发布后可被发现 |

## 7. 来源清单（全部核验链接）

- 规范主页（2026-07-28）：https://modelcontextprotocol.io/specification/2026-07-28
- 2026-07-28 完整 Changelog：https://modelcontextprotocol.io/specification/2026-07-28/changelog
- 历史版本索引：https://modelcontextprotocol.io/specification/2025-11-25 等（路径将日期替换即可）
- Python SDK：https://pypi.org/project/mcp/ · 文档 https://py.sdk.modelcontextprotocol.io/ · 仓库 https://github.com/modelcontextprotocol/python-sdk
- TypeScript SDK：https://www.npmjs.com/package/@modelcontextprotocol/server · v2 文档 https://ts.sdk.modelcontextprotocol.io/v2/ · 仓库 https://github.com/modelcontextprotocol/typescript-sdk
- 遗留 v1 单包：https://www.npmjs.com/package/@modelcontextprotocol/sdk
- Inspector：https://github.com/modelcontextprotocol/inspector （npm: `@modelcontextprotocol/inspector`）
- 第三方 fastmcp 框架：https://pypi.org/project/fastmcp/ · https://github.com/jlowin/fastmcp
- Claude Code MCP 接入文档：https://code.claude.com/docs/en/mcp
- MCP 文档总入口：https://modelcontextprotocol.io/docs
