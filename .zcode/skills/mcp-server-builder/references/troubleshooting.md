# 排错手册 —— 症状 → 原因 → 修复

> 按"症状"索引。通用第一步永远是：**手动运行启动命令**（Python `uv run mcp run server.py` / TS `npx tsx src/index.ts`），直接看真实报错。

## 1. 宿主"服务器连不上 / 状态 failed"

| 症状 | 原因 | 修复 |
|------|------|------|
| 手动启动立即退出/traceback | 代码 bug、依赖缺失、Python 版本 <3.10、Node <20 | 读 traceback 修复；`uv sync` / `npm install`；核对运行时版本 |
| 手动启动正常挂起，宿主仍连不上 | 路径不是绝对路径 | 所有 `command`/`args` 改绝对路径（脚本 + `uv`/`npx` 本体，`where`/`which` 查） |
| 同上 | 宿主没重启 | 完全退出宿主再开（Claude Desktop 关窗口不算） |
| 同上 | 配置文件名/顶层键错误 | 对照 `references/host-configuration.md` §5 速查表（`servers` vs `mcpServers`） |
| 同上 | Windows 下 `npx`/`uv` 不在宿主 PATH | `where uv` 取绝对路径写入 `command` |
| TS 服务器报 `Cannot use import statement outside a module` | 缺 ESM 标记 | `npm pkg set type=module` |
| 连上立刻断开，日志见 "corrupt"/JSON 解析错误 | **stdout 被污染** | 见 §3 |

## 2. 工具不显示 / 模型不调用

| 症状 | 原因 | 修复 |
|------|------|------|
| 服务器已连接但工具列表空 | 注册代码在工厂/`__main__` 外未执行；或服务器对象命名导致 `mcp run` 没找到 | 确认注册在 `MCPServer` 实例上且模块级可达（`mcp`/`server`/`app`） |
| Inspector 能看到、宿主看不到 | 宿主缓存 | 重启宿主；VS Code 执行 `MCP: Reset Cached Tools` |
| Copilot 从不调用 | Chat 不在 Agent 模式 | 切到 Agent 模式 |
| 模型看到了但选不中/选错 | description 太弱、参数无 describe、万能工具名 | 按 `tool-design-guide.md` §1 重写 |
| 工具数量爆炸选择混乱 | 一次注册过多相近工具 | 合并同类（参数区分）或拆分服务器 |

## 3. stdout 污染（stdio 第一大坑）

症状：宿主报协议错误/断连；Python 侧有时表现为"偶发"（缓冲输出在退出时才冲进 stdout）。

排查：

1. 全仓搜索写 stdout 的代码：`console.log`、`process.stdout.write`、Python `print(`、被重定向到 stdout 的子进程输出。
2. Python 特别注意：导入期 `print`、`subprocess.run` 未捕获的 stdout、退出时才 flush 的缓冲 `print`。SDK 只能改道"运行期间 flush"的输出。
3. 修复原则：**所有人类可读输出走 stderr** —— Python 用 `logging`（默认 stderr handler 逐条 flush）；TS 用 `console.error`。
4. 调试完删干净临时调试输出；需要长期保留的调试用环境变量开关（如 `DEBUG=1` 时才输出到 stderr）。

## 4. 工具调用失败类

| 症状 | 原因 | 修复 |
|------|------|------|
| 返回 `Input validation error: ...` | 参数不满足 schema | 这是**正常防线**：模型会按校验消息重试；若频繁发生则改 schema 设计（必填过多/约束过紧） |
| 返回 `Error executing tool <name>`，无细节 | handler 抛了未预期异常（崩溃） | 看服务器日志的 ERROR 堆栈；把可预期分支显式 `raise ToolError` |
| 模型收到错误文案但不当失败处理 | 用了 `return "Error..."` | 改 `ToolError` / `isError: true`（细节见各 SDK 文档错误处理节） |
| 上游 API 报 401/403 | 宿主子进程环境里没有密钥 | 密钥写入宿主配置条目 `env` 字段（宿主不继承你 shell 的环境变量） |
| 偶发 `MCPError: -32001 REQUEST_TIMEOUT`（Python 客户端） | 请求超时 | 调大 Client 超时（浮点秒）；工具内部慢操作加进度上报 |
| HTTP 部署 413 | 请求体超 Python SDK 默认 4 MiB | `run(max_request_body_size=...)` 调大 |
| HTTP 部署 403 | Host/Origin 校验拦截 | 检查访问域名与校验配置（部署安全项，确认来源合法后放行） |

## 5. Claude Desktop 专属

| 症状 | 原因 | 修复 |
|------|------|------|
| `mcp install` 报 `Claude app not found` | 配置目录不存在 | 安装并运行一次 Claude Desktop |
| 配置改了没生效 | 窗口关闭 ≠ 退出 | 托盘完全退出后重开 |
| 找不到服务器日志 | 位置记错 | macOS `~/Library/Logs/Claude/mcp-server-<NAME>.log`；Windows `%APPDATA%\Claude\logs\` |

## 6. Inspector 问题

| 症状 | 原因 | 修复 |
|------|------|------|
| `mcp dev` 报 npx 缺失 | Inspector 是 Node 应用 | 安装 Node ≥20；或直接 `npx @modelcontextprotocol/inspector <cmd>` |
| Inspector 打不开浏览器 | 无头/远程环境 | 改用自动化测试（内存 Client），见各 SDK 文档 §测试 |
| Connect 后 Tools 页空 | 见 §2 前两行 | 同上 |

## 7. 版本与兼容

| 症状 | 原因 | 修复 |
|------|------|------|
| `TypeError: MCPServer.__init__() got an unexpected keyword argument 'port'` | transport 参数给了构造函数 | 移到 `run(port=...)` |
| `ImportError: cannot import name 'FastMCP'` | 装了 v2 却按 v1 教程写 | `from mcp.server import MCPServer`；见 `v1-to-v2-migration.md` |
| 旧宿主连不上 v2 服务器 | 协议 era 差异 | v2 SDK 默认兼容旧 era 客户端；仍失败则让宿主升级，或查 SDK [legacy clients 文档](https://py.sdk.modelcontextprotocol.io/run/legacy-clients/) |
| 团队成员装到不同 SDK 版本行为不一致 | 未锁版本 | Python `mcp>=2,<3` 锁大版本；TS `@modelcontextprotocol/server@^2`；提交 lockfile |
