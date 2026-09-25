# MCP Server Builder

从一句功能描述，产出一个**可直接运行、可接入真实宿主**的 MCP（Model Context Protocol）服务器：技术选型 → 脚手架 → 实现 tools/resources/prompts → 三层测试 → 宿主注册 → 排错与分发，全流程一条龙。

- 基于 **官方 SDK 当前 v2 稳定线**（Python `mcp` 2.x 的 `MCPServer`；TypeScript `@modelcontextprotocol/server` 2.x），全部 API 经联网核验（核验日期 2026-09-25，对应 MCP 规范 2026-07-28）
- 内置 v1 → v2 对照手册，旧教程照抄前先"翻译"
- 内置排错手册，专治"宿主连不上 / 工具不显示 / 调用报错"

## 目录结构

```
mcp-server-builder/
├── SKILL.md                        # 技能主文件（工作流与约束）
├── README.md                       # 本文件
├── references/                     # 按需加载的参考文档
│   ├── python-sdk-v2.md            #   Python SDK v2 API 详解
│   ├── typescript-sdk-v2.md        #   TypeScript SDK v2 API 详解
│   ├── spec-notes.md               #   MCP 规范版本演进与生态术语（含全部来源链接）
│   ├── tool-design-guide.md        #   工具设计原则与反模式
│   ├── host-configuration.md       #   四大宿主注册配置 + 远程部署清单
│   ├── v1-to-v2-migration.md       #   v1 → v2 迁移对照
│   └── troubleshooting.md          #   排错手册（症状 → 原因 → 修复）
├── scripts/
│   └── check_env.py                # 环境自检（Python/uv/Node/npx/SDK 版本）
└── templates/
    ├── python/                     # Python 路线脚手架（src/server.py + pyproject.toml）
    └── typescript/                 # TS 路线脚手架（src/index.ts + package.json）
```

## 安装方式

1. 将 `mcp-server-builder/` 目录复制到项目的 `.claude/skills/` 下（或用户级 `~/.claude/skills/`）
2. 重启 Claude Code
3. 用 `/mcp-server-builder` 或触发关键词激活

## 使用方式

### 斜杠命令

```
/mcp-server-builder <功能描述>
# 例：/mcp-server-builder 写一个查询 GitHub 仓库 issue 的 MCP server
# 例：/mcp-server-builder 把我们内部的 FAQ 文档做成 MCP 资源服务器，Python
```

### 自动触发

当输入包含以下关键词时自动激活：**写MCP / 开发MCP / MCP server / MCP服务器 / MCP工具 / 创建MCP / mcp builder / MCP集成 / Model Context Protocol**

## Workflow 说明

技能被激活后按七步执行：

1. **需求分析与技术决策** — 选语言（Python/TS）、选传输（stdio/Streamable HTTP）、选原语（Tool/Resource/Prompt），输出决策摘要
2. **环境检查与依赖核实** — `check_env.py` 自检 + 联网核实 SDK 最新版本与目标 API 现状
3. **项目脚手架** — 复制模板到 `./mcp-servers/<server-name>/` 并安装依赖
4. **实现** — 按 v2 API 实现，强制工具描述规范、错误双轨模型、stderr 日志纪律、密钥环境变量化
5. **测试** — 冒烟启动（手动跑命令）→ MCP Inspector 交互测试 → 内存 Client 自动化测试
6. **宿主接入与排错** — 生成 Claude Code / Claude Desktop / VS Code / Cursor 的注册配置；连不上时按排错手册定位
7. **分发与交付报告** — 打包方式、工具清单、测试结论、配置片段、来源链接

## 技术细节

### 依赖（对被生成的 MCP 服务器项目而言）

- Python 路线：Python ≥ 3.10，推荐 [uv](https://docs.astral.sh/uv/)；SDK `mcp>=2`（含 `[cli]` extra 可用 `mcp dev/run/install`）
- TypeScript 路线：Node.js ≥ 20；`@modelcontextprotocol/server@^2`、`zod@^4`、`tsx`（免构建直接运行）
- 调试：MCP Inspector（`npx @modelcontextprotocol/inspector`，需 npx）

### 使用的工具（技能自身）

- `Read` / `Write` / `Edit` — 读写生成的服务器代码与配置
- `Bash` — 脚手架、安装依赖、运行测试与 Inspector
- `WebSearch` / `WebFetch` — 核实 SDK 版本与第三方 API（多源交叉验证）
- `Glob` / `Grep` — 扩展既有服务器时定位代码

### 输出说明

- 生成的服务器项目默认落在 `./mcp-servers/<server-name>/`（可指定其他目录）
- 交付报告包含：工具清单、测试结论、宿主注册片段、需用户自填的密钥项、已知限制、来源链接

## 注意事项

- **stdout 纪律**：stdio MCP 服务器的 stdout 是协议通道，本技能生成的代码严格只向 stderr 打日志 —— 用户自行改动时若向 stdout 打印，宿主会立即断连（这是 MCP 开发第一大坑）
- **密钥安全**：凭证一律走环境变量 + 宿主配置的 `env` 字段；技能会拒绝硬编码密钥
- **v1 项目**：维护既有 v1（FastMCP / `@modelcontextprotocol/sdk` 1.x）项目时，技能默认**不**主动迁移，v1→v2 是破坏性升级，需用户明确同意
- **信息时效**：SDK/规范信息核验于 2026-09-25；技能在生成前会重新核实最新版本，离线环境会在报告中声明"未联网复核"
- 第三方 `fastmcp` 框架（PyPI 独立包）与官方 SDK 是两个生态；未点名时技能默认官方 SDK

## 参考

- MCP 规范（2026-07-28）：https://modelcontextprotocol.io/specification/2026-07-28
- 规范 Changelog：https://modelcontextprotocol.io/specification/2026-07-28/changelog
- Python SDK：https://pypi.org/project/mcp/ · https://py.sdk.modelcontextprotocol.io/ · https://github.com/modelcontextprotocol/python-sdk
- TypeScript SDK：https://www.npmjs.com/package/@modelcontextprotocol/server · https://ts.sdk.modelcontextprotocol.io/v2/ · https://github.com/modelcontextprotocol/typescript-sdk
- MCP Inspector：https://github.com/modelcontextprotocol/inspector
- Claude Code MCP 接入：https://code.claude.com/docs/en/mcp
- MCP 文档总入口：https://modelcontextprotocol.io/docs
