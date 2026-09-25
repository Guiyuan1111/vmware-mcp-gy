# Skill Blueprint: vmrun/vmcli 命令行封装生成器

> 自动生成自 codebase-analyzer
> 分析时间：2026-09-25_153605
> 源模块路径：`C:\Users\Administrator\Desktop\zcode\mcp开发\VMware\vmware-mcp-gy\src\vmware_mcp\vmrun.py` 与 `...\vmcli.py`

---

## 1. 基本信息

| 字段 | 值 |
|------|-----|
| **推荐 Skill 名称** | `vmware-mcp-cli-wrapper-generator` |
| **用途** | 依据 vmrun/vmcli 官方命令规格，按项目既有同构模板生成封装方法、T() 声明与 call_tool 分支三件套 |
| **AI 替代等级** | 🤖 完全 AI 化 |
| **实施优先级** | 🥇 Quick Win |
| **源文件数** | 3（vmrun.py、vmcli.py、server.py） |
| **源代码行数** | ~1058（vmrun.py 222 + vmcli.py 308 + server.py 528） |

## 2. 触发场景与关键词

- "为 vmrun 添加一个新命令的封装"
- "接入 vmcli 的 XXX 模块"
- "vmware-mcp 缺少 YYY 操作，补一个工具"
- "把 vmrun/vmcli 的全部命令做成 MCP 工具"

**推荐 description 触发词（用于 SKILL.md frontmatter）：**

```yaml
description: >-
  Generate vmrun/vmcli command wrapper methods plus MCP tool declarations and
  dispatch branches for the vmware-mcp project. Triggered by: "封装vmrun命令",
  "接入vmcli", "新增CLI工具", "wrap vmrun command", "add vmcli tool".
```

## 3. 输入输出契约

### 主要函数接口（生成目标必须符合的既有接口）

| 函数 | 输入 | 输出 | 副作用 | 代码位置 |
|------|------|------|--------|---------|
| `VMRun._run` | `command: str, *args: str, guest_user: str = "", guest_pass: str = ""` | `str`（stdout） | 启动 vmrun 子进程 | `vmrun.py:16-38` |
| `VMCli._run` | `vmx_path: str \| None, module: str, command: str, *args: str` | `str`（stdout） | 启动 vmcli 子进程 | `vmcli.py:18-36` |
| 封装方法（模板） | `vmx_path: str` + 命令参数 + guest 类的 `user/password` | `str` | 委托 `_run` | 如 `vmrun.py:41-42`、`vmcli.py:42-43` |

### 方法模板（从现有代码提取）

```python
# vmrun 模板（简单后缀变体）
async def stop(self, vmx_path: str, hard: bool = False) -> str:
    return await self._run("stop", vmx_path, "hard" if hard else "soft")
# 位置: vmrun.py:44-45

# vmcli 模板（三段式）
async def snapshot_take(self, vmx_path: str, name: str) -> str:
    return await self._run(vmx_path, "Snapshot", "Take", "-n", name)
# 位置: vmcli.py:42-43
```

### 数据模型

```python
// 命令规格输入（AI 生成所需的结构化输入）
interface CommandSpec {
  channel: 'vmrun' | 'vmcli';
  method: string;        // Python 方法名（snake_case）
  vmrunVerb?: string;    // vmrun: "start" / "listSnapshots" ...
  module?: string;       // vmcli: "Snapshot" / "Chipset" ...
  subcommand?: string;   // vmcli: "Take" / "SetVCpuCount" ...
  args: Array<{ flag: string; param: string; type: 'str'|'int'|'bool' }>;
  guestAuth?: boolean;   // 是否需要 -gu/-gp 或 -u/-P
  flags?: string[];      // 可选布尔标志，如 "-noWait"（vmrun.py:130-135）
}
```

### 错误码

| 错误码 / 异常 | 触发条件 | 传播行为 |
|--------------|---------|---------|
| `RuntimeError("vmrun failed: {msg}")` | vmrun 非零退出（`vmrun.py:32-36`） | 直抛至 MCP 框架层 |
| `RuntimeError("vmcli failed: {msg}")` | vmcli 非零退出（`vmcli.py:32-34`） | 直抛 |
| 空路径副作用 | `get_vmx_path` 返回 `""`（`server.py:45`） | CLI 报错，语义降级 |

## 4. 依赖清单

### 外部服务

| 服务 | 用途 | 接口 |
|------|------|------|
| vmrun.exe | VMware 通用自动化 CLI | 默认 `C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe`，可用 `VMRUN_PATH` 覆盖（`vmrun.py:11-14`） |
| vmcli.exe | VMware 高级配置 CLI | 默认同目录，可用 `VMCLI_PATH` 覆盖（`vmcli.py:13-16`） |

### 内部模块

| 模块 | 用途 | 关键接口 |
|------|------|---------|
| `server.py::get_vmx_path` | VM ID → vmx 路径 | `async (vm_id: str) -> str`（`server.py:34-45`） |
| `server.py::T` | 工具声明 | `T(name, desc, props, required)`（`server.py:48-53`） |
| `server.py::call_tool` | 分发接入点 | vmrun 分支区 `server.py:287-378`；vmcli 分支区 `server.py:381-510` |

### 配置项

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `VMRUN_PATH` | string | 上述默认路径 | vmrun 可执行文件路径 |
| `VMCLI_PATH` | string | 上述默认路径 | vmcli 可执行文件路径 |

## 5. Skill 工作流设计

````markdown
## Workflow / Steps

### Step 1: 解析输入
确定通道（vmrun / vmcli）、目标命令及其官方参数规格；核对命令名/选项字母
（如 vmcli 的 -n/-p/-s 语义随子命令变化）必须与官方文档或既有代码一致。

### Step 2: 生成封装方法
- vmrun：在 vmrun.py 对应分区（Power/General/Snapshot/...，见 vmrun.py:40-222 的
  # === 注释分区）追加方法；布尔后缀用三元（"hard"/"soft"），guest 方法末尾
  guest_user=user, guest_pass=password
- vmcli：按 模块+子命令+选项 三段式追加；vmx_path 传 str 或 None（template_deploy
  型命令传 None，参照 vmcli.py:177-178）
- 禁止复刻 run_program 的 args.split()（vmrun.py:138）对含空格参数的分词缺陷

### Step 3: 接入 server.py
- T() 声明放对应分区（vmrun: server.py:87-141；vmcli: server.py:145-223）
- call_tool 分支：vm_id 一律经 vmx() 转换（server.py:236-237）；
  可选参数用 a.get(key, default)，默认值对照同类工具（如 clone_type 默认
  "linked"，server.py:290）
- 注意 server.py 分支调用名与 vmcli 方法名的映射差异（如 T("chipset_set_cores")
  → vmcli.chipset_set_cores_per_socket，server.py:424）

### Step 4: 同步 README
工具写入 README.md 对应通道表格，重算汇总表数字。

### Step 5: 自检
Grep 校验：T() 名称与 elif 分支名称逐字符一致；方法名无重复；required 列表
与 schema 属性匹配。
````

### 建议的 Constraints

````markdown
## Constraints
- Always 委托 _run()，禁止在封装方法里直接 create_subprocess_exec
- Always guest 凭据参数命名 user/password 且默认空串（与既有 46+10 个方法一致）
- Always 三件套一次生成，名称逐字符对齐（避免静默落空，server.py:512-514）
- Always 新方法的返回类型注解为 str（stdout 文本）
- Never 为命令执行添加 try/except 吞异常或"失败返回 OK"
- Never 将 guest 密码写入日志或返回值
- Never 更改 _run 的现有签名与错误消息前缀（"vmrun failed: " / "vmcli failed: "）
````

## 6. 所需工具权限

| 工具 | 用途 | 必需性 |
|------|------|--------|
| `Read` | 读取三源文件与官方文档摘录 | 必需 |
| `Edit` | 修改 vmrun.py / vmcli.py / server.py / README.md | 必需 |
| `Grep` | 名称一致性自检 | 必需 |

**建议 allowed-tools：** `Read Edit Grep`

## 7. 使用示例

### ✅ Do This

```text
输入: "封装 vmcli 的 Nvme SetPresent 并暴露为工具"
输出: vmcli.py 新增 nvme_set_present（已存在，复用 vmcli.py:291-292）+
      server.py 确认 T("nvme_set_present") 与 elif 分支均已存在（server.py:217,500）→
      报告"已完整接入，无需改动"
```

### ❌ Not This

```text
输入: "封装 vmcli 的 Nvme SetPresent 并暴露为工具"
错误输出: 在 vmrun.py 里也加一份同名方法；或 T() 的 props 里把 adapter 写成
          必填但 elif 分支用 a["present"] 顺序取参错位；或忽略 server.py:424
          的方法名映射差异直接调 vmcli.chipset_set_cores（不存在的方法名）
```

## 8. 参考材料

- 源文件：`src/vmware_mcp/vmrun.py`（46 个模板）、`src/vmware_mcp/vmcli.py`（67 个模板）、`src/vmware_mcp/server.py:87-510`（接入点）
- 死方法清单（可优先暴露）：`vmcli.py:191-192`（vm_create）、`vmcli.py:258-259`（hgfs_set_present）
- 关键代码片段：两类方法模板见本 Blueprint 第 3 节
