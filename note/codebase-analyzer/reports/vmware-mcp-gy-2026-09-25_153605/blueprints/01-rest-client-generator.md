# Skill Blueprint: REST 客户端封装生成器

> 自动生成自 codebase-analyzer
> 分析时间：2026-09-25_153605
> 源模块路径：`C:\Users\Administrator\Desktop\zcode\mcp开发\VMware\vmware-mcp-gy\src\vmware_mcp\client.py`

---

## 1. 基本信息

| 字段 | 值 |
|------|-----|
| **推荐 Skill 名称** | `vmware-mcp-rest-client-generator` |
| **用途** | 依据 VMware Workstation REST API 端点规格，按项目既有模板批量生成 `VMwareClient` 方法并接入 MCP 工具 |
| **AI 替代等级** | 🤖 完全 AI 化 |
| **实施优先级** | 🥇 Quick Win |
| **源文件数** | 2（client.py、server.py） |
| **源代码行数** | ~94（client.py） |

## 2. 触发场景与关键词

- "为 vmware-mcp 添加一个新的 REST API 端点封装"
- "把 VMwareClient 的方法暴露成 MCP 工具"
- "客户端里缺一个 update_nic 的 MCP 工具，补上"
- "批量接入 vmrest 的 mactoip 接口"

**推荐 description 触发词（用于 SKILL.md frontmatter）：**

```yaml
description: >-
  Generate VMwareClient REST API wrapper methods and expose them as MCP tools
  following the vmware-mcp project conventions. Triggered by: "新增REST端点",
  "接入vmrest接口", "暴露MCP工具", "add REST endpoint", "expose as MCP tool".
```

## 3. 输入输出契约

### 主要函数接口（生成目标必须符合的既有接口）

| 函数 | 输入 | 输出 | 副作用 | 代码位置 |
|------|------|------|--------|---------|
| `_request` | `method: str, path: str, **kwargs` | `Any`（JSON dict/list，空 body → `None`） | 发起 HTTP 请求 | `client.py:14-20` |
| 端点方法（模板） | 端点参数（str/int/dict） | `dict`/`list[dict]`/`None` | HTTP 调用 | 如 `client.py:23-24` |

### 方法模板（从现有代码提取）

```python
async def list_vms(self) -> list[dict]:
    return await self._request("GET", "/vms")
# 位置: client.py:23-24
# 变体: 路径参数用 f-string 内插（client.py:27）；请求体用 json=（client.py:30）；
#       查询参数用 params=（client.py:43）；删除类方法返回类型注解为 None（client.py:32-33）
```

### 数据模型

```python
# VM 清单项（REST /vms 返回，server.py:242-243 消费其字段）
interface VMItem {
  id: string;    // 如 "vm-564d-abcd"
  path: string;  // vmx 绝对路径
}
```

### 错误码

| 错误码 / 异常 | 触发条件 | 传播行为 |
|--------------|---------|---------|
| `httpx.HTTPStatusError` | REST 返回 4xx/5xx（`raise_for_status`，`client.py:17`） | 直抛至 MCP 框架层 |
| `httpx.ConnectError` | vmrest 不可达 | 直抛 |
| 返回 `None` | HTTP 2xx 且空 body（`client.py:18-20`） | 正常返回 |

## 4. 依赖清单

### 外部服务

| 服务 | 用途 | 接口 |
|------|------|------|
| vmrest REST API | VMware Workstation HTTP 接口 | `http://{host}:{port}/api`（`client.py:11`），认证 Basic（`client.py:12`） |

### 内部模块

| 模块 | 用途 | 关键接口 |
|------|------|---------|
| `client.py::VMwareClient` | 生成目标类 | `__init__(host, port, username, password)`（`client.py:10-12`） |
| `server.py::T` | 工具声明工厂 | `T(name, desc, props, required)`（`server.py:48-53`） |
| `server.py::call_tool` | 分发接入点 | REST 分支区 `server.py:240-284` |

### 配置项

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `VMWARE_HOST` | string | localhost | REST 主机（`server.py:19`） |
| `VMWARE_PORT` | int | 8697 | REST 端口（`server.py:20`） |
| `VMWARE_USERNAME` / `VMWARE_PASSWORD` | string | 空 | 认证（username 为空则免认证，`client.py:12`） |

## 5. Skill 工作流设计

````markdown
## Workflow / Steps

### Step 1: 解析输入
从用户输入提取：端点路径模板、HTTP 动词、方法名、参数表（路径参数/查询参数/请求体）、
返回类型。对照 VMware REST API 文档确认字段拼写。

### Step 2: 生成 client 方法
按模板在 client.py 对应分区（VM Management / Power / NIC / Shared Folders / Networks，
见 client.py:22-94 的分区注释）追加方法：
- 路径参数必须经 urllib.parse.quote 编码（禁止复刻 client.py:27 的裸 f-string 缺陷）
- 删除类方法返回注解 None
- 命名与既有方法风格一致（snake_case 动词开头：list_/get_/create_/update_/delete_）

### Step 3: 接入 server.py（三件套）
- 在 list_tools() 的 REST 分区追加 T(...) 声明（server.py:61-83 同区）
- 在 call_tool() 的 REST 分支区追加 elif（server.py:240-284 同区）
- snake_case → camelCase 字段映射逐一核对（参照 server.py:281 的 guestIp/guestPort）
- 删除类分支需手工补 {"status": "deleted"}（参照 server.py:249-250）

### Step 4: 同步 README
将新工具写入 README.md 对应分区表格，并重算头部汇总表数字（历史漂移
已于 2026-09-25 复审修复，新增工具后仍需重算）。

### Step 5: 错误处理
确认新代码不吞异常（直抛风格）；HTTP/子进程调用不得添加静默默认成功路径。
````

### 建议的 Constraints

````markdown
## Constraints
- Always 复用 _request()，禁止绕过它直接使用 httpx
- Always 路径参数做 URL 编码（修复既有缺陷而非复刻）
- Always 保留 raise_for_status() 与空 body 返回 None 的语义（client.py:17-20）
- Always 三件套（wrapper 方法 + T() 声明 + call_tool 分支）一次生成，保证名称逐字符一致
- Never 为 REST 调用添加"失败时返回 OK"的逻辑
- Never 在报告中或代码注释中写入 VMWARE_PASSWORD 等凭据实际值
- Never 移除或改动既有 19 个 REST 方法的签名
````

## 6. 所需工具权限

| 工具 | 用途 | 必需性 |
|------|------|--------|
| `Read` | 读取 client.py / server.py / README.md | 必需 |
| `Edit` | 修改三个文件 | 必需 |
| `Grep` | 检查名称唯一性与既有引用 | 必需 |
| `WebFetch` | 查询 VMware REST API 文档（可选） | 可选 |

**建议 allowed-tools：** `Read Edit Grep WebFetch`

## 7. 使用示例

### ✅ Do This

```text
输入: "为 VMwareClient 添加 GET /vmnet/{vmnet}/mactoip 封装并暴露为 MCP 工具"
输出: client.py 新增 get_mac_to_ips（已存在则复用 client.py:81-82）+
      server.py 新增 T("network_mactoip_list", ...) + 对应 elif 分支 +
      README 表格同步更新
```

### ❌ Not This

```text
输入: 同上
错误输出: 在 client.py 里新写一个独立的 httpx.get() 调用（绕过 _request）；
          T() 声明写成 "network-mactoip-list"（连字符与 elif 分支 "network_mactoip_list"
          不一致 → 调用永远静默落空返回 OK，server.py:512-514 会掩盖该错误）
```

## 8. 参考材料

- 源文件：`src/vmware_mcp/client.py`（模板全集）、`src/vmware_mcp/server.py:48-53,56-224,227-284`（接入点）
- 死方法清单（可优先暴露）：`client.py:52-53`（update_nic）、`client.py:68-69`（update_shared_folder）、`client.py:81-82`（get_mac_to_ips）、`client.py:84-85`（update_mac_to_ip）
- 关键代码片段：方法模板见本 Blueprint 第 3 节
