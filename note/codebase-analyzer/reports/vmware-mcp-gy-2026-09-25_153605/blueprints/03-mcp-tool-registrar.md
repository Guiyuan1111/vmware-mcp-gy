# Skill Blueprint: MCP 工具三件套注册器

> 自动生成自 codebase-analyzer
> 分析时间：2026-09-25_153605
> 源模块路径：`C:\Users\Administrator\Desktop\zcode\mcp开发\VMware\vmware-mcp-gy\src\vmware_mcp\server.py`

---

## 1. 基本信息

| 字段 | 值 |
|------|-----|
| **推荐 Skill 名称** | `vmware-mcp-tool-registrar` |
| **用途** | 端到端地把一个新能力（REST 端点或 CLI 命令）注册为 vmware-mcp 的 MCP 工具：T() 声明 + call_tool 分支 + 底层封装 + README 同步，并保证三方名称一致 |
| **AI 替代等级** | 🤖 完全 AI 化 |
| **实施优先级** | 🥈 Strategic（消除声明/实现漂移的结构性措施） |
| **源文件数** | 1（server.py，联动 client/vmrun/vmcli/README） |
| **源代码行数** | 528 |

## 2. 触发场景与关键词

- "给 vmware-mcp 加一个新工具"
- "把 XXX 能力暴露成 MCP 工具"
- "call_tool 里缺 XXX 的分支，补上"
- "这个工具的 schema 怎么写"

**推荐 description 触发词（用于 SKILL.md frontmatter）：**

```yaml
description: >-
  Register a new MCP tool end-to-end in the vmware-mcp server: tool schema
  declaration, dispatch branch, underlying wrapper, and README sync.
  Triggered by: "新增MCP工具", "加一个工具", "注册工具", "add MCP tool",
  "expose tool", "工具注册".
```

## 3. 输入输出契约

### 主要函数接口

| 函数 | 输入 | 输出 | 副作用 | 代码位置 |
|------|------|------|--------|---------|
| `T` | `name: str, desc: str, props: dict, required: list \| None = None` | `Tool`（含 inputSchema） | 无 | `server.py:48-53` |
| `list_tools` | 无（装饰器 `@server.list_tools()`） | `list[Tool]`（130 个） | 无 | `server.py:56-224` |
| `call_tool` | `name: str, arguments: dict` | `list[TextContent]` | 经适配器触碰 VMware | `server.py:227-514` |
| `vmx`（闭包） | `vm_id: str` | `str` | 可能触发 REST 全量拉取 | `server.py:236-237` |

### 数据模型

```python
// T() 的 inputSchema 结构（server.py:50-53）
interface ToolSchema {
  type: 'object';
  properties: Record<string, { type: 'string'|'integer'|'boolean'; enum?: string[] }>;
  required?: string[];
}

// call_tool 统一出口的返回规则（server.py:512-514）
// str  → 原样（空串 → "OK"）；dict/list → json.dumps(indent=2)；None/空值 → "OK"（未匹配分支与空结果均被静默掩盖，缺陷）
```

### 错误码

| 错误码 / 异常 | 触发条件 | 行为 |
|--------------|---------|------|
| `KeyError` | 必填参数缺失（`a["..."]` 直取） | 直抛 |
| `RuntimeError` | vmrun/vmcli 失败 | 直抛 |
| 静默 `"OK"` | **name 未匹配任何分支**（`server.py:232,512-514`） | 【缺陷】必须避免新增此类路径 |

## 4. 依赖清单

| 类型 | 名称 | 用途 | 接口 |
|------|------|------|------|
| 框架 | mcp SDK | Server/Tool/TextContent/stdio | `server.py:5-7` |
| 内部模块 | `VMwareClient` / `VMRun` / `VMCli` | 三条执行通道 | `server.py:9-11`；实例化点 `server.py:229-231` |
| 环境配置 | `VMWARE_*`、`VMRUN_PATH`、`VMCLI_PATH` | 运行时配置 | `server.py:19-22`、`vmrun.py:11-14`、`vmcli.py:13-16` |
| 文档 | `README.md` 工具表 | 注册后同步 | `README.md:48-190` |

### 分区路由表（server.py 内的既有分区，新增工具必须落对区域）

| 前缀/名称族 | T() 声明区 | 分支区 | 通道 |
|------------|-----------|--------|------|
| `vm_*` / `network_*` | `server.py:59-83` | `server.py:240-284` | REST（vm_id 原样，不转 vmx） |
| `vmrun_*` | `server.py:85-141` | `server.py:287-378` | vmrun（vm_id 经 vmx() 转换） |
| `snapshot_*` / `guest_*` / `mks_*` / `chipset_*` / `tools_*` / `template_*` / `disk_*` / `config_*` / `power_*` / `ethernet_*` / `hgfs_*` / `serial_*` / `sata_*` / `nvme_*` / `vprobes_*` | `server.py:143-223` | `server.py:381-510` | vmcli（vm_id 经 vmx() 转换；`template_deploy` 除外） |

## 5. Skill 工作流设计

````markdown
## Workflow / Steps

### Step 1: 解析输入
确定：工具名（snake_case，按前缀族命名）、通道（REST/vmrun/vmcli）、
参数表（名称/类型/必填/enum/默认值）、目标底层方法（已有则复用，无则先走
Blueprint 01/02 生成封装）。

### Step 2: 声明工具
在 server.py 对应分区追加 T(...)：
- name 与将来 elif 分支比较的字面量逐字符一致
- enum 用在受限取值上（参照 vm_power_set 的 state enum，server.py:68）
- 删除/覆盖类工具必须把标识参数设为 required

### Step 3: 添加分支
在 call_tool 对应分区追加 elif：
- vmrun/vmcli 通道：await vmx(a["vm_id"]) 前置转换
- REST 通道：snake_case → camelCase 映射逐一核对（参照 server.py:281）
- 可选参数 a.get(key, default)；默认值必须与同类工具一致
  （gui 默认 True，server.py:296；hard 默认 False，server.py:298）
- 删除类分支补 {"status": "deleted"}（参照 server.py:249-250）

### Step 4: 一致性自检
- Grep T("<name>") 与 elif name == "<name>" 必须同时恰好出现一次
- schema 的 required 中的每个键，在 elif 分支必须以 a["key"] 直取或显式校验
- enum 值必须与分支内可能传入底层的取值一致

### Step 5: 同步 README
写入对应通道表格 + 重算头部汇总表数字（当前 README.md:7-13 声称 117，
实际 130，必须一并修正）。
````

### 建议的 Constraints

````markdown
## Constraints
- Always 三件套（声明/分支/封装）+ README 四点一次完成，禁止只改其一
- Always 工具名 snake_case 且与 elif 字面量逐字符一致
- Always vmrun/vmcli 通道先经 vmx() 转换（template_deploy 型除外）
- Never 依赖"未匹配分支返回 OK"（server.py:512-514）；新增工具必须显式匹配
- Never 在 desc 中泄露主机路径、凭据或内网拓扑细节
- Never 复用已被占用的工具名（当前 130 个，以 list_tools 全文为准）
- Never 把 password 属性写入 Tool 的 inputSchema 默认值
````

## 6. 所需工具权限

| 工具 | 用途 | 必需性 |
|------|------|--------|
| `Read` | 读取 server.py 与 README.md | 必需 |
| `Edit` | 修改 server.py / README.md（及按需 client/vmrun/vmcli） | 必需 |
| `Grep` | 名称一致性自检与查重 | 必需 |

**建议 allowed-tools：** `Read Edit Grep`

## 7. 使用示例

### ✅ Do This

```text
输入: "给 vmware-mcp 添加 'vm_get_snapshot_tree' 工具（vmcli Snapshot query）"
输出: server.py:145 区追加 T("snapshot_tree", ..., ["vm_id"])（命名按 vmcli 前缀族）+
      server.py:381 区追加 elif name == "snapshot_tree":
        result = await vmcli.snapshot_list(await vmx(a["vm_id"]))  +
      README vmcli 表新增一行 + 汇总表 65→66
```

### ❌ Not This

```text
输入: 同上
错误输出: 只加了 T() 声明没加 elif（调用静默落空返回 OK）；或 vm_id 直接
          传给 vmcli.snapshot_list 而没经 vmx() 转换（VM ID 不是路径，vmcli
          无法识别）；或工具名写成 "snapshot-tree"（连字符导致永不匹配）
```

## 8. 参考材料

- 源文件：`src/vmware_mcp/server.py`（`T`：48-53；声明区：56-224；分发区：227-514；出口：512-514；main：517-524）
- 已知缺陷（新增代码不得复刻）：静默 OK（server.py:512-514）、空 vmx 路径（server.py:45）
- 死方法（优先暴露候选）：`vmcli.py:191-192`、`vmcli.py:258-259`、`client.py:52-53,68-69,81-85`
