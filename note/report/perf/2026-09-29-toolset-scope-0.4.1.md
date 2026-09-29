# 0.4.1 工具面作用域（VMWARE_TOOLS）性能与工程质量报告

日期：2026-09-29 ｜ 依据：mcp-server-builder skill 规模指引对照 + 647 次历史调用分布

## 1. 动机

skill 对照结论（见 0.4.1 release note）：140 个工具全部暴露与「单服务器 ≤20 个含义相近工具」的规模指引相悖；skill 给出的解法是拆分多服务器，但那要求改动宿主注册（触碰兼容红线）。本版改为**单服务器内按家族裁剪暴露面**（env 一行配置），宿主注册零改动。

历史调用分布（09-22→09-29，647 次）支持按家族收缩：vmrun 系占绝对主力，REST/vmcli 低频。

## 2. 工具列表负载实测（`json.dumps(tool.model_dump())` 全量序列化）

| 作用域 | 工具数 | 列表负载 | 占全量 |
| --- | --- | --- | --- |
| 全量（默认 `all`/缺省） | 140 | 74.8KB | 100% |
| `vmrun` | 56 | 35.7KB | 48% |
| `vmcli` | 68 | 31.5KB | 42% |
| `rest` | 22 | 10.6KB | 14% |
| `core` | 3 | 1.5KB | 2% |
| `vmrun,rest` | 75 | 44.8KB | 60% |

tools/list 负载直接进入每个会话的模型上下文：`VMWARE_TOOLS=vmrun` 下每个会话省 ~39KB 工具描述（约一半选工具噪声），对工具选择命中率与上下文占用均为正向。

## 3. 家族划分与守卫语义

- 家族映射取自路由表适配器标记（单一事实来源，新增工具自动归类）：vmrun 53 / vmcli 65 / rest 19 / core 3（=140，无未映射）。
- `vm_resolve` / `vm_health` / `vm_log_tail` 划为 **core**：诊断与 vm_id 解析入口，任何作用域保留（错误提示链路引用它们，隐藏会自断指引）。
- 域外调用在 `call_tool` 入口即结构化拒绝（先于护栏与 vm_id 解析）；非法值回退全量并 stderr 告警（可用性优先）。

## 4. 协议符合性门（新增 `benchmark/protocol_conformance.py`）

对真实启动命令的 stdio JSON-RPC 全程会话断言，9/9 通过、无需 VMware：
握手与 serverInfo、全量工具列表、未知工具结构化错误、破坏性工具 dry-run 护栏（协议层在线验证）、错误路径后服务器存活、`VMWARE_TOOLS=rest` 作用域裁剪（22 工具）、域外调用守卫拒绝、**stdout 全程 JSON-RPC 零污染**（skill 红线项的自动化）。

## 5. 回归

- 单元测试 70/70（新增作用域 6 项：默认全量、家族全覆盖、vmrun 保留 core、组合与非法值、入口守卫、list_tools 过滤）。
- 黄金快照 141/141 逐字节一致（默认无 env 时行为零变化，金快照不受影响）。
- 复测命令：`python benchmark/protocol_conformance.py`；负载口径见本文 §2。

## 6. 明确不做

- 迁移 v2 SDK（skill 自身 v1 兼容注 + 项目兼容红线）。
- 拆分为多个 MCP 服务器（需改宿主注册；`VMWARE_TOOLS` 已覆盖同等收益）。
- structuredContent/结构化输出（改变客户端可见契约）。
