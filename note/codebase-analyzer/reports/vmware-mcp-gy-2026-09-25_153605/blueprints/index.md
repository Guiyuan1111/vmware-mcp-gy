# Skill Blueprint 索引

> 项目：vmware-mcp（vmware-mcp-gy）
> 分析时间：2026-09-25_153605
> 覆盖：130 个 MCP 工具、5 个源文件、151 个函数/方法

| # | Blueprint | 组件 | AI 等级 | 优先级 | 文件 |
|---|-----------|------|---------|--------|------|
| 1 | rest-client-generator | VMwareClient REST 端点封装生成 | 🤖 完全 AI 化 | 🥇 Quick Win | [blueprints/01-rest-client-generator.md](01-rest-client-generator.md) |
| 2 | cli-wrapper-generator | vmrun/vmcli 命令封装生成 | 🤖 完全 AI 化 | 🥇 Quick Win | [blueprints/02-cli-wrapper-generator.md](02-cli-wrapper-generator.md) |
| 3 | mcp-tool-registrar | MCP 工具三件套端到端注册 | 🤖 完全 AI 化 | 🥈 Strategic | [blueprints/03-mcp-tool-registrar.md](03-mcp-tool-registrar.md) |
| 4 | tool-consistency-auditor | 声明/实现/文档一致性审计 | 🤖 完全 AI 化 | 🥇 Quick Win | [blueprints/04-tool-consistency-auditor.md](04-tool-consistency-auditor.md) |

## 实施路线图

### 立即实施（Quick Win）
1. **工具一致性审计** (`blueprints/04-tool-consistency-auditor.md`) — 存在已确认漂移（README 声称 117 个工具，实际 130 个，`README.md:7-13` vs `server.py:61-223`），零风险直接修复
2. **REST 封装生成** (`blueprints/01-rest-client-generator.md`) — 可顺带暴露 4 个死方法（`client.py:52-53,68-69,81-85`）
3. **CLI 封装生成** (`blueprints/02-cli-wrapper-generator.md`) — 113 个同构方法模板已验证，可批量复制

### 规划实施（Strategic）
4. **MCP 工具三件套注册** (`blueprints/03-mcp-tool-registrar.md`) — 将"声明/分支/封装/文档"固化为原子流程，消除 `list_tools()`（`server.py:56-224`）与 `call_tool()`（`server.py:239-510`）之间靠字符串人工对齐的结构性风险

### 人工主导（不出 Blueprint）
- 架构演进决策：通道去重、错误体系统一、安全加固（guest 凭据 argv 传递 `vmrun.py:20-21`、TLS 校验关闭 `client.py:15`）——AI 仅提供分析输入

## 使用说明

每个 Blueprint 文件包含创建对应 Skill 所需的完整设计规格（触发词、接口契约、依赖清单、Workflow、Constraints、工具权限、Do/Not 示例）。加载对应 Blueprint 文件即可生成标准 SKILL.md。

---

> 各 Blueprint 均已把本项目的已知缺陷（静默 OK：`server.py:512-514`；空 vmx 路径：`server.py:45`；URL 未编码：`client.py:27`；无超时：`vmrun.py:30`/`client.py:15-16`）写入 Constraints，生成新代码时不得复刻。
