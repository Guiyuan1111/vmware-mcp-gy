# Skill Blueprint: 工具声明/实现/文档一致性审计器

> 自动生成自 codebase-analyzer
> 分析时间：2026-09-25_153605
> 源模块路径：`C:\Users\Administrator\Desktop\zcode\mcp开发\VMware\vmware-mcp-gy\src\vmware_mcp\server.py` 与 `...\README.md`

---

## 1. 基本信息

| 字段 | 值 |
|------|-----|
| **推荐 Skill 名称** | `vmware-mcp-tool-consistency-auditor` |
| **用途** | 静态审计 vmware-mcp 的三方一致性：`list_tools()` 声明、`call_tool()` 分支、底层封装方法、README 文档，输出差异报告并可自动修复 |
| **AI 替代等级** | 🤖 完全 AI 化 |
| **实施优先级** | 🥇 Quick Win（存在已确认的实际漂移：README 117 vs 代码 130） |
| **源文件数** | 5（全部源文件 + README.md） |
| **源代码行数** | ~1350（含 README） |

## 2. 触发场景与关键词

- "检查工具声明和实现是否一致"
- "README 的工具数量对不对"
- "有没有工具只声明没实现"
- "审计一下 vmware-mcp"

**推荐 description 触发词（用于 SKILL.md frontmatter）：**

```yaml
description: >-
  Audit consistency between MCP tool declarations (list_tools), dispatch
  branches (call_tool), wrapper methods, and README docs in vmware-mcp;
  report and optionally fix drift. Triggered by: "工具一致性检查", "审计工具",
  "README对不上", "audit tools", "check tool drift".
```

## 3. 输入输出契约

### 主要函数接口（被审计对象）

| 对象 | 输入 | 输出 | 代码位置 |
|------|------|------|---------|
| `list_tools` | 无 | 130 个 `T(...)` 声明 | `server.py:56-224` |
| `call_tool` | `name, arguments` | 约 130 个 `elif name == "..."` 分支 | `server.py:239-510` |
| 三适配器公共方法 | 各自参数 | `dict/str/None` | `client.py:23-94`、`vmrun.py:41-222`、`vmcli.py:39-308` |
| README 工具表 | 无 | 1 汇总表 + 3 明细表 | `README.md:7-13,48-190` |

### 已确认的漂移基线（本蓝图的价值证明）

| 检查项 | README 声称 | 代码实际 | 证据 |
|--------|------------|---------|------|
| 工具总数 | 117 | **130** | `README.md:7` vs `server.py:61-223`（grep 'T("' = 130） |
| REST 工具数 | 20 | **19** | `README.md:11` vs `server.py:61-83` |
| vmrun 工具数 | 54 | **46** | `README.md:12` vs `server.py:87-141` |
| vmcli 工具数 | 43 | **65** | `README.md:13` vs `server.py:145-223` |
| 死方法（已实现未暴露） | 未披露 | 6 个 | `client.py:52-53,68-69,81-85`、`vmcli.py:191-192,258-259` |

### 输出模型

```text
审计报告结构：
1. 声明集 A = list_tools() 中全部 T() 的 name（应 130 个）
2. 分支集 B = call_tool() 中全部 elif name == "..." 的字面量（应 130 个）
3. 封装集 C = 三适配器全部公共方法
4. 文档集 D = README 明细表工具名
差集报告：A-B（只声明未实现 → 运行时静默 OK）、B-A（死分支）、
          A-C（声明无封装 → AttributeError）、C-A（死方法）、A-D / D-A（文档漂移）
```

### 错误码

| 错误码 | 触发条件 | 说明 |
|--------|---------|------|
| `DECL_NO_BRANCH` | name 在 A 不在 B | 调用静默返回 OK（`server.py:512-514` 掩盖） |
| `BRANCH_NO_DECL` | name 在 B 不在 A | 永不可达代码 |
| `DECL_NO_METHOD` | 声明调用的适配器方法不存在 | 运行时 AttributeError |
| `METHOD_NO_DECL` | 死方法 | 如 `vmcli.vm_create`（`vmcli.py:191-192`） |
| `DOC_DRIFT` | A 与 D 不一致 | 如 README.md:7 的 117 vs 130 |

## 4. 依赖清单

| 类型 | 名称 | 用途 | 接口 |
|------|------|------|------|
| 内部模块 | server.py / client.py / vmrun.py / vmcli.py | 审计对象 | 见第 3 节 |
| 文档 | README.md | 审计对象 | `README.md:7-190` |
| 工具 | Grep/正则 | 提取 `T("...")` 与 `elif name == "..."` | — |

## 5. Skill 工作流设计

````markdown
## Workflow / Steps

### Step 1: 提取声明集
正则提取 server.py 中全部 T("name", ...) 调用的 name 与参数属性；按分区归类
（REST/vmrun/vmcli）。

### Step 2: 提取分支集
正则提取 call_tool() 中全部 elif name == "..." 字面量；同时提取每个分支
调用的适配器方法名与实参 a["..."]/a.get("...") 清单。

### Step 3: 提取封装集与文档集
列出三适配器全部 async 公共方法名；正则提取 README 三个明细表的工具名列
与汇总表数字。

### Step 4: 差集分析
按第 3 节输出模型计算 A-B/B-A/A-C/C-A/A-D/D-A 五组差集；对 A-B 特别标注
"会触发静默 OK 缺陷"（server.py:512-514）；交叉校验每个分支的实参键是否
都在对应 T() 的 properties 中（参数漂移检测）。

### Step 5: 修复建议与执行
- DOC_DRIFT：重生成 README 汇总表与明细表（数字 + 行序按 server.py 分区顺序）
- METHOD_NO_DECL：与用户确认暴露（走 Blueprint 03）或删除
- DECL_NO_BRANCH / BRANCH_NO_DECL：按 Blueprint 03 补齐或移除
输出修复前后对照，保持零静默改动。
````

### 建议的 Constraints

````markdown
## Constraints
- Always 输出五组差集的完整清单，不允许只报数量
- Always 对每条差异引用文件:行号
- Never 静默修改源文件；修复前必须先输出审计报告并获得确认
- Never 把"未匹配分支返回 OK"（server.py:512-514）当作一致的表现——那是缺陷
- Never 依据 README 推断代码行为（README 已知漂移），以代码为准
````

## 6. 所需工具权限

| 工具 | 用途 | 必需性 |
|------|------|--------|
| `Read` | 读取全部源文件与 README | 必需 |
| `Grep` | 正则提取声明/分支/方法名 | 必需 |
| `Edit` | 经确认后修复 README 或补齐三件套 | 可选（仅修复模式） |

**建议 allowed-tools：** `Read Grep`（审计模式）/ `Read Grep Edit`（修复模式）

## 7. 使用示例

### ✅ Do This

```text
输入: "审计 vmware-mcp 的工具一致性"
输出: 报告——声明 130 / 分支 130（示例数字以实测为准）/ 封装 136 / 文档 117；
      DOC_DRIFT: README.md:7 声称 117，实际 130（REST 19、vmrun 46、vmcli 65）；
      METHOD_NO_DECL: client.update_nic (client.py:52) 等 6 个死方法；
      并给出修复方案
```

### ❌ Not This

```text
输入: "审计 vmware-mcp 的工具一致性"
错误输出: 直接把 README 的 117 改成 130 但不列差异明细；或把死方法
          client.get_mac_to_ips (client.py:81) 无确认地删除（可能是有意
          为后续暴露预留的能力，删除需用户决策）
```

## 8. 参考材料

- 源文件：`src/vmware_mcp/server.py:56-224`（声明区）、`server.py:239-510`（分支区）、`server.py:512-514`（静默 OK 出口）
- 漂移证据：`README.md:7-13` vs `server.py:61-223`
- 死方法证据：`client.py:52-53,68-69,81-85`、`vmcli.py:191-192,258-259`
- 已知缺陷基线：`server.py:45`（空 vmx 路径）、`server.py:512-514`（静默 OK）、`client.py:15`（verify=False）、`client.py:27`（URL 未编码）
