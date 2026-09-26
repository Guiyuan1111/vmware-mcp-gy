"""VMware MCP Server - Complete implementation with REST API, vmcli, and vmrun."""

import asyncio
import glob
import json
import logging
import os
import sys
import time

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ToolAnnotations

from .client import VMwareClient
from .errors import ToolError, make_hint
from .runtime import enc_password, env_int
from .vmcli import VMCli
from .vmrun import VMRun

logger = logging.getLogger("vmware_mcp")

server = Server("vmware-mcp")
_vm_path_cache: dict[str, str] = {}
_enc_passwords: dict[str, str] = {}
_vm_path_lock = asyncio.Lock()
_UNHANDLED = object()

# 破坏性工具清单：删除/强停/杀进程/清空配置类。调用前须 confirm:true，否则返回 dry-run 预览；
# VMWARE_READ_ONLY=1 时直接拒绝。vm_power_set 特例：仅 state=off（硬断电）按破坏性处理。
DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({
    "vm_delete", "vmrun_delete",
    "vmrun_stop", "vmrun_reset", "vmrun_suspend",
    "vmrun_snapshot_delete", "vmrun_snapshot_revert",
    "snapshot_delete", "snapshot_revert",
    "vmrun_rm", "vmrun_rmdir", "vmrun_kill", "guest_kill",
    "vmrun_portforward_delete", "network_portforward_delete",
    "vm_nic_delete", "vm_folder_delete", "vmrun_shared_remove",
    "ethernet_purge", "serial_purge", "sata_purge", "nvme_purge",
    "disk_extend",
})

# 纯查询类工具：向宿主声明 readOnlyHint（写宿主文件的截图类与全部有副作用的工具不在列）
READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "vm_list", "vm_get", "vm_power_get", "vm_nic_list", "vm_ip_get", "vm_folder_list",
    "network_list", "network_portforward_list",
    "vmrun_list", "vmrun_snapshot_list", "vmrun_file_exists", "vmrun_dir_exists",
    "vmrun_ls", "vmrun_ps", "vmrun_tools_state", "vmrun_guest_ip",
    "vmrun_host_networks", "vmrun_portforward_list", "vmrun_var_read",
    "snapshot_list", "guest_ps", "guest_ls", "guest_env", "mks_query",
    "chipset_query", "tools_query", "disk_query", "config_query", "power_query",
    "ethernet_query", "hgfs_query", "serial_query", "sata_query", "nvme_query",
    "vprobes_query",
    "vm_resolve", "vm_health", "vm_log_tail",
})


def _is_destructive(name: str, arguments: dict) -> bool:
    if name == "vm_power_set":
        return arguments.get("state") == "off"
    return name in DESTRUCTIVE_TOOLS


def _read_only_mode() -> bool:
    return os.getenv("VMWARE_READ_ONLY", "").strip().lower() in ("1", "true", "yes", "on")

_client: VMwareClient | None = None
_vmrun: VMRun | None = None
_vmcli: VMCli | None = None


def get_client() -> VMwareClient:
    global _client
    if _client is None:
        _client = VMwareClient(
            host=os.getenv("VMWARE_HOST", "localhost"),
            port=int(os.getenv("VMWARE_PORT", "8697")),
            username=os.getenv("VMWARE_USERNAME", ""),
            password=os.getenv("VMWARE_PASSWORD", ""),
            verify=os.getenv("VMWARE_TLS_VERIFY", "").strip().lower() in ("1", "true", "yes", "on"),
        )
    return _client


def get_vmcli() -> VMCli:
    global _vmcli
    if _vmcli is None:
        _vmcli = VMCli()
    return _vmcli


def get_vmrun() -> VMRun:
    global _vmrun
    if _vmrun is None:
        _vmrun = VMRun()
    return _vmrun


async def get_vmx_path(vm_id: str) -> str:
    """Convert VM ID to vmx path. Supports both VM IDs and direct vmx paths."""
    # If vm_id is already a vmx path, return it directly
    if vm_id.endswith(".vmx") or "/" in vm_id or "\\" in vm_id:
        return vm_id

    async with _vm_path_lock:
        path = _vm_path_cache.get(vm_id)
        if path and not os.path.exists(path):
            # 缓存指向的 vmx 已不存在（VM 被移动/删除）→ 失效重建
            _vm_path_cache.pop(vm_id, None)
            path = None
        if path is None:
            client = get_client()
            vms = await client.list_vms()
            for vm in vms:
                _vm_path_cache[vm["id"]] = vm["path"]
            path = _vm_path_cache.get(vm_id, "")

    if not path:
        raise ToolError(
            f"Unknown VM id: {vm_id}",
            hint="vm_id 须为 vmx 绝对路径或 REST vm_id；先调用 vm_list 查看可用 VM（缓存失效时重新 vm_list）",
        )
    return path


def _vmx_encryption(vmx_path: str) -> str:
    """读 vmx 文件中的 encryptionType；无该键返回 none，文件不可读返回 unknown。"""
    try:
        with open(vmx_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "encryptionType" in line and "=" in line:
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        return "unknown"
    return "none"


def _log_tail(path: str, lines: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            data = f.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-lines:])
    except OSError as e:
        return f"(无法读取 {path}: {e})"


def _ocr_image(image_path: str) -> dict:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return {"ok": False, "image": image_path, "hint": "OCR 引擎未安装：pip install 'vmware-mcp[ocr]'"}
    try:
        engine = RapidOCR()
        detected, _elapsed = engine(image_path)
    except Exception as e:
        return {
            "ok": False,
            "image": image_path,
            "error": f"{type(e).__name__}: {e}",
            "hint": "图片不可读或 OCR 失败：检查截图文件是否有效（宿主锁屏时截屏可能全黑）",
        }
    lines = [{"text": item[1], "score": round(float(item[2]), 3)} for item in (detected or [])]
    return {"ok": True, "image": image_path, "lines": lines, "text": "\n".join(item["text"] for item in lines)}


def T(name: str, desc: str, props: dict, required: list | None = None, annotations: ToolAnnotations | None = None) -> Tool:
    """Helper to create Tool definitions."""
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return Tool(name=name, description=desc, inputSchema=schema, annotations=annotations)


def _error_content(payload: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]


def _truncate_output(text: str) -> str:
    """超长输出截断，防止大规模 list/ps 结果撑爆模型上下文；VMWARE_MAX_OUTPUT 可调（0/负数禁用截断）。"""
    limit = env_int("VMWARE_MAX_OUTPUT", 20000)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"\n...（已截断：原长 {len(text)} 字符，仅显示前 {limit}；可用 VMWARE_MAX_OUTPUT 调整或缩小查询范围）"


def _structured(fn):
    """call_tool 异常统一转结构化 JSON 文本响应；未预期异常也不再静默。
    每次调用记一行 stderr 日志（工具名/成败/耗时，VMWARE_LOG_LEVEL=INFO 开启）。"""

    async def wrapper(name: str, arguments: dict) -> list[TextContent]:
        started = time.monotonic()
        try:
            content = await fn(name, arguments)
        except ToolError as e:
            e.tool = e.tool or name
            logger.warning("tool=%s ok=False duration_ms=%d read_only=%s timeout=%s",
                           name, int((time.monotonic() - started) * 1000), e.read_only, e.timeout)
            return _error_content(e.to_dict())
        except httpx.HTTPStatusError as e:
            body = e.response.text[:2000] if e.response is not None else ""
            logger.warning("tool=%s ok=False duration_ms=%d http_status=%s",
                           name, int((time.monotonic() - started) * 1000),
                           getattr(e.response, "status_code", None))
            return _error_content({
                "ok": False,
                "tool": name,
                "status": getattr(e.response, "status_code", None),
                "error": str(e),
                "body": body,
                "hint": make_hint(body),
            })
        except Exception as e:
            logger.warning("tool=%s ok=False duration_ms=%d unexpected=%s: %s",
                           name, int((time.monotonic() - started) * 1000), type(e).__name__, e)
            return _error_content({
                "ok": False,
                "tool": name,
                "error": f"{type(e).__name__}: {e}",
                "hint": make_hint(str(e)),
            })
        logger.info("tool=%s ok=True duration_ms=%d", name, int((time.monotonic() - started) * 1000))
        return content

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


_TOOLS_CACHE: list[Tool] | None = None


def _build_tools() -> list[Tool]:
    """构建全部 137 个工具定义（含 enc_pass/vm_id/confirm 注入与 annotations）。
    结果进程内缓存：工具集在运行期不变，tools/list 每次重建纯属浪费。"""
    tools = [
        # ==================== SERVER ====================
        T("set_vm_encryption_password", "server｜预存加密 VM 的密码（按解析后的 vmx 路径记忆，进程内存）。加密 VM 调 vmrun 系工具前先调用；持久方案用 env VMWARE_ENC_PASSWORD。无副作用", {"vm_id": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "password"]),
        T("vm_resolve", "server｜模糊名/路径片段解析 VM：返回 vmx 绝对路径 + 电源状态 + 加密类型。消除 vm_id 歧义的第一入口。只读", {"query": {"type": "string"}}, ["query"]),
        T("vm_health", "server｜一次调用拿全 VM 体检：运行中/Tools 状态/IP/加密类型/.vmem+.vmss 空闲挂起判据/vmware.log 尾 20 行。VM\"不动了\"先调这个。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("vm_log_tail", "server｜读 vmware.log 尾部 N 行（默认 50）。VMX idle exit=空闲挂起；password required=加密问题。只读", {"vm_id": {"type": "string"}, "lines": {"type": "integer"}}, ["vm_id"]),
        T("screenshot_ocr", "server｜VM 截屏并 OCR 成文本（纯文字模型可直接读画面）。需可选依赖 pip install 'vmware-mcp[ocr]'。只读（写宿主图片）", {"vm_id": {"type": "string"}, "output_path": {"type": "string"}}, ["vm_id", "output_path"]),
        # ==================== REST API ====================
        # VM Management
        T("vm_list", "REST/vmrest｜列出全部已注册 VM（id/path）。需 vmrest 运行（默认 8697）。只读；结果写入服务端 vm_id→vmx 缓存", {}),
        T("vm_get", "REST/vmrest｜读 VM 配置详情。需 vmrest。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("vm_create", "REST/vmrest｜按 vm_id 克隆新 VM。需 vmrest。副作用：创建新 VM", {"vm_id": {"type": "string"}, "name": {"type": "string"}}, ["vm_id", "name"]),
        T("vm_delete", "REST/vmrest｜删除 VM 注册。需 vmrest。副作用：不可逆删除", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("vm_update", "REST/vmrest｜改 VM 的 CPU/内存配置。需 vmrest。副作用：改 VM 硬件配置", {"vm_id": {"type": "string"}, "cpu": {"type": "integer"}, "memory": {"type": "integer"}}, ["vm_id"]),
        # VM Power (REST)
        T("vm_power_get", "REST/vmrest｜查电源状态。需 vmrest。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("vm_power_set", "REST/vmrest｜电源状态机：on/off/shutdown/suspend/pause/unpause。off=硬断电，shutdown=软关机（需 guest 配合）。与 vmrun_*/power_* 重叠，需 vmrest 时用本工具。副作用：电源变化", {"vm_id": {"type": "string"}, "state": {"type": "string", "enum": ["on", "off", "shutdown", "suspend", "pause", "unpause"]}}, ["vm_id", "state"]),
        # VM Network Adapters
        T("vm_nic_list", "REST/vmrest｜列 VM 网卡。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("vm_nic_create", "REST/vmrest｜添加网卡（bridged/nat/hostonly/custom）。副作用：改 VM 网络硬件", {"vm_id": {"type": "string"}, "type": {"type": "string", "enum": ["bridged", "nat", "hostonly", "custom"]}}, ["vm_id", "type"]),
        T("vm_nic_delete", "REST/vmrest｜删除网卡（按 index）。副作用：改 VM 网络硬件", {"vm_id": {"type": "string"}, "index": {"type": "integer"}}, ["vm_id", "index"]),
        T("vm_ip_get", "REST/vmrest｜取 VM IP。VM 需运行且已报告 IP。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        # VM Shared Folders
        T("vm_folder_list", "REST/vmrest｜列 HGFS 共享文件夹。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("vm_folder_create", "REST/vmrest｜添加共享文件夹。副作用：改 VM 共享目录", {"vm_id": {"type": "string"}, "folder_id": {"type": "string"}, "host_path": {"type": "string"}, "flags": {"type": "integer"}}, ["vm_id", "folder_id", "host_path"]),
        T("vm_folder_delete", "REST/vmrest｜移除共享文件夹。副作用：改 VM 共享目录", {"vm_id": {"type": "string"}, "folder_id": {"type": "string"}}, ["vm_id", "folder_id"]),
        # Host Networks
        T("network_list", "REST/vmrest｜列宿主机虚拟网络（vmnet）。只读", {}),
        T("network_create", "REST/vmrest｜新建虚拟网络。副作用：改宿主机网络配置", {"name": {"type": "string"}, "type": {"type": "string", "enum": ["bridged", "nat", "hostonly"]}}, ["name", "type"]),
        T("network_portforward_list", "REST/vmrest｜列 vmnet 端口转发。只读", {"vmnet": {"type": "string"}}, ["vmnet"]),
        T("network_portforward_set", "REST/vmrest｜设置端口转发。副作用：改宿主机端口映射", {"vmnet": {"type": "string"}, "protocol": {"type": "string", "enum": ["tcp", "udp"]}, "port": {"type": "integer"}, "guest_ip": {"type": "string"}, "guest_port": {"type": "integer"}}, ["vmnet", "protocol", "port", "guest_ip", "guest_port"]),
        T("network_portforward_delete", "REST/vmrest｜删除端口转发。副作用：改宿主机端口映射", {"vmnet": {"type": "string"}, "protocol": {"type": "string"}, "port": {"type": "integer"}}, ["vmnet", "protocol", "port"]),

        # ==================== VMRUN ====================
        # General
        T("vmrun_list", "vmrun｜列运行中的 VM（仅运行中；全部 VM 用 vm_list）。只读", {}),
        T("vmrun_clone", "vmrun｜克隆 VM（full/linked，可基于 snapshot）。长任务（600s 上限，VMWARE_TIMEOUT_LONG 可调）。副作用：磁盘占用", {"vm_id": {"type": "string"}, "dest_path": {"type": "string"}, "clone_type": {"type": "string", "enum": ["full", "linked"]}, "snapshot": {"type": "string"}, "clone_name": {"type": "string"}}, ["vm_id", "dest_path"]),
        T("vmrun_upgrade", "vmrun｜升级 VM 硬件版本（长任务）。副作用：改 VM 格式", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("vmrun_delete", "vmrun｜删除 VM。副作用：不可逆删除（含磁盘）", {"vm_id": {"type": "string"}}, ["vm_id"]),
        # Power (vmrun)
        T("vmrun_start", "vmrun｜启动/恢复 VM。gui=显示窗口，nogui=后台；对挂起 VM（有 .vmem/.vmss）是无损恢复而非冷启动。加密 VM 需 enc_pass。副作用：电源 on", {"vm_id": {"type": "string"}, "gui": {"type": "boolean"}}, ["vm_id"]),
        T("vmrun_stop", "vmrun｜关机。默认 soft 优雅关机（需 guest 配合），hard=true 硬断电。加密 VM 需 enc_pass。副作用：电源 off", {"vm_id": {"type": "string"}, "hard": {"type": "boolean"}}, ["vm_id"]),
        T("vmrun_reset", "vmrun｜重启。默认 soft，hard=true 强制。副作用：电源重启，guest 未保存数据丢失", {"vm_id": {"type": "string"}, "hard": {"type": "boolean"}}, ["vm_id"]),
        T("vmrun_suspend", "vmrun｜挂起到磁盘。默认 soft，hard=true 强制。副作用：电源挂起", {"vm_id": {"type": "string"}, "hard": {"type": "boolean"}}, ["vm_id"]),
        T("vmrun_pause", "vmrun｜暂停（内存保持）。副作用：进入暂停态", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("vmrun_unpause", "vmrun｜恢复暂停。副作用：退出暂停态", {"vm_id": {"type": "string"}}, ["vm_id"]),
        # Snapshot (vmrun)
        T("vmrun_snapshot_list", "vmrun｜列快照（show_tree=true 树状）。只读", {"vm_id": {"type": "string"}, "show_tree": {"type": "boolean"}}, ["vm_id"]),
        T("vmrun_snapshot_take", "vmrun｜拍快照。副作用：磁盘占用", {"vm_id": {"type": "string"}, "name": {"type": "string"}}, ["vm_id", "name"]),
        T("vmrun_snapshot_delete", "vmrun｜删快照（delete_children=true 连锁删子快照）。副作用：不可逆删快照", {"vm_id": {"type": "string"}, "name": {"type": "string"}, "delete_children": {"type": "boolean"}}, ["vm_id", "name"]),
        T("vmrun_snapshot_revert", "vmrun｜回滚到快照。副作用：丢弃快照之后的全部变更", {"vm_id": {"type": "string"}, "name": {"type": "string"}}, ["vm_id", "name"]),
        # Guest File Operations
        T("vmrun_file_exists", "vmrun｜查 guest 文件是否存在。前提：VM 运行+装 Tools+guest 凭据（user/password）。只读", {"vm_id": {"type": "string"}, "path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "path"]),
        T("vmrun_dir_exists", "vmrun｜查 guest 目录是否存在。前提：运行+Tools+凭据。只读", {"vm_id": {"type": "string"}, "path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "path"]),
        T("vmrun_ls", "vmrun｜列 guest 目录内容。前提：运行+Tools+凭据。只读", {"vm_id": {"type": "string"}, "path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "path"]),
        T("vmrun_mkdir", "vmrun｜guest 内建目录。前提：运行+Tools+凭据。副作用：改 guest 文件系统", {"vm_id": {"type": "string"}, "path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "path"]),
        T("vmrun_rmdir", "vmrun｜guest 内删目录。前提：运行+Tools+凭据。副作用：改 guest 文件系统", {"vm_id": {"type": "string"}, "path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "path"]),
        T("vmrun_rm", "vmrun｜guest 内删文件。前提：运行+Tools+凭据。副作用：改 guest 文件系统", {"vm_id": {"type": "string"}, "path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "path"]),
        T("vmrun_rename", "vmrun｜guest 内重命名。前提：运行+Tools+凭据。副作用：改 guest 文件系统", {"vm_id": {"type": "string"}, "old_path": {"type": "string"}, "new_path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "old_path", "new_path"]),
        T("vmrun_copy_to", "vmrun｜单文件 宿主→guest。前提：运行+Tools+凭据。整目录用 vmrun_copy_dir_to", {"vm_id": {"type": "string"}, "host_path": {"type": "string"}, "guest_path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "host_path", "guest_path"]),
        T("vmrun_copy_from", "vmrun｜单文件 guest→宿主。前提：运行+Tools+凭据。整目录用 vmrun_copy_dir_from", {"vm_id": {"type": "string"}, "guest_path": {"type": "string"}, "host_path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "guest_path", "host_path"]),
        T("vmrun_temp_file", "vmrun｜在 guest 内建临时文件。前提：运行+Tools+凭据。副作用：guest 文件系统", {"vm_id": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id"]),
        T("vmrun_copy_dir_to", "vmrun｜递归复制宿主目录树→guest（include/exclude 传逗号分隔后缀如 \".txt,.log\"；自动逐级建父目录）。前提：运行+Tools+凭据。副作用：guest 文件系统", {"vm_id": {"type": "string"}, "host_path": {"type": "string"}, "guest_path": {"type": "string"}, "include": {"type": "string"}, "exclude": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "host_path", "guest_path"]),
        T("vmrun_copy_dir_from", "vmrun｜递归复制 guest 目录树→宿主（基于 vmrun ls 容错解析，解析失败行计入 skipped）。前提：运行+Tools+凭据。副作用：宿主文件系统", {"vm_id": {"type": "string"}, "guest_path": {"type": "string"}, "host_path": {"type": "string"}, "include": {"type": "string"}, "exclude": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "guest_path", "host_path"]),
        # Guest Process
        T("vmrun_run", "vmrun｜guest 内执行程序。args 传数组（每项一个参数，推荐）或整串（作为单个参数透传，不再按空格拆分）。前提：运行+Tools+凭据。副作用：guest 内进程", {"vm_id": {"type": "string"}, "program": {"type": "string"}, "args": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}, "no_wait": {"type": "boolean"}, "interactive": {"type": "boolean"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "program"]),
        T("vmrun_script", "vmrun｜guest 内执行脚本（interpreter 如 cmd.exe / bash，script 为脚本文件路径）。前提：运行+Tools+凭据。副作用：guest 内进程", {"vm_id": {"type": "string"}, "interpreter": {"type": "string"}, "script": {"type": "string"}, "no_wait": {"type": "boolean"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "interpreter", "script"]),
        T("vmrun_ps", "vmrun｜列 guest 进程。前提：运行+Tools+凭据。只读", {"vm_id": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id"]),
        T("vmrun_kill", "vmrun｜杀 guest 进程（pid 可由 vmrun_ps 获得）。前提：运行+Tools+凭据。副作用：终止 guest 进程", {"vm_id": {"type": "string"}, "pid": {"type": "integer"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "pid"]),
        # Shared Folders (vmrun)
        T("vmrun_shared_enable", "vmrun｜启用 HGFS 共享目录功能。副作用：VM 共享配置", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("vmrun_shared_disable", "vmrun｜禁用 HGFS 共享目录功能。副作用：VM 共享配置", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("vmrun_shared_add", "vmrun｜添加共享文件夹（name 为 guest 内显示名）。副作用：VM 共享配置", {"vm_id": {"type": "string"}, "name": {"type": "string"}, "host_path": {"type": "string"}}, ["vm_id", "name", "host_path"]),
        T("vmrun_shared_remove", "vmrun｜移除共享文件夹。副作用：VM 共享配置", {"vm_id": {"type": "string"}, "name": {"type": "string"}}, ["vm_id", "name"]),
        T("vmrun_shared_set", "vmrun｜切换共享文件夹读写态（writable=true 读写）。副作用：VM 共享配置", {"vm_id": {"type": "string"}, "name": {"type": "string"}, "host_path": {"type": "string"}, "writable": {"type": "boolean"}}, ["vm_id", "name", "host_path"]),
        # Device
        T("vmrun_device_connect", "vmrun｜连接命名设备（如 cdrom0:0、floppy0:0）。副作用：设备状态", {"vm_id": {"type": "string"}, "device": {"type": "string"}}, ["vm_id", "device"]),
        T("vmrun_device_disconnect", "vmrun｜断开命名设备。副作用：设备状态", {"vm_id": {"type": "string"}, "device": {"type": "string"}}, ["vm_id", "device"]),
        # Variables
        T("vmrun_var_read", "vmrun｜读 VM 变量（runtimeConfig=vmx / guestEnv=环境变量 / guestVar=运行时）。只读", {"vm_id": {"type": "string"}, "var_type": {"type": "string", "enum": ["runtimeConfig", "guestEnv", "guestVar"]}, "name": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "var_type", "name"]),
        T("vmrun_var_write", "vmrun｜写 VM 变量（同上三种）。副作用：写 VM/guest 变量", {"vm_id": {"type": "string"}, "var_type": {"type": "string", "enum": ["runtimeConfig", "guestEnv", "guestVar"]}, "name": {"type": "string"}, "value": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "var_type", "name", "value"]),
        # Screen/Input
        T("vmrun_screenshot", "vmrun｜VM 截屏存为宿主 PNG。VM 需运行。只读（写宿主文件）", {"vm_id": {"type": "string"}, "output_path": {"type": "string"}}, ["vm_id", "output_path"]),
        T("vmrun_keystrokes", "vmrun｜向 guest 发键序列（需 Tools；VIX 键序列语法如 \"Hello<Enter>\"）。副作用：guest 键盘输入", {"vm_id": {"type": "string"}, "keystrokes": {"type": "string"}}, ["vm_id", "keystrokes"]),
        # Tools/Network
        T("vmrun_tools_install", "vmrun｜安装 VMware Tools（可能分钟级，挂起等待属正常）。副作用：guest 内安装软件", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("vmrun_tools_state", "vmrun｜查 Tools 状态（installed/running）。guest_* 操作前预检用。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("vmrun_guest_ip", "vmrun｜取 guest IP（wait=true 等待 DHCP 分配）。前提：Tools running。只读", {"vm_id": {"type": "string"}, "wait": {"type": "boolean"}}, ["vm_id"]),
        T("vmrun_host_networks", "vmrun｜列宿主机虚拟网络。与 network_list（REST）重叠。只读", {}),
        T("vmrun_portforward_list", "vmrun｜列 NAT 网络端口转发。只读", {"network": {"type": "string"}}, ["network"]),
        T("vmrun_portforward_set", "vmrun｜设置 NAT 端口转发。与 network_portforward_set（REST）重叠。副作用：改宿主机端口映射", {"network": {"type": "string"}, "protocol": {"type": "string"}, "host_port": {"type": "integer"}, "guest_ip": {"type": "string"}, "guest_port": {"type": "integer"}, "description": {"type": "string"}}, ["network", "protocol", "host_port", "guest_ip", "guest_port"]),
        T("vmrun_portforward_delete", "vmrun｜删除 NAT 端口转发。副作用：改宿主机端口映射", {"network": {"type": "string"}, "protocol": {"type": "string"}, "host_port": {"type": "integer"}}, ["network", "protocol", "host_port"]),

        # ==================== VMCLI ====================
        # Snapshot
        T("snapshot_list", "vmcli｜列快照。vmrun_snapshot_list 有等价工具；加密 VM 建议走 vmrun 系（enc_pass 支持）。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("snapshot_take", "vmcli｜拍快照。副作用：磁盘占用", {"vm_id": {"type": "string"}, "name": {"type": "string"}}, ["vm_id", "name"]),
        T("snapshot_revert", "vmcli｜回滚快照。副作用：丢弃快照后变更", {"vm_id": {"type": "string"}, "name": {"type": "string"}}, ["vm_id", "name"]),
        T("snapshot_delete", "vmcli｜删快照（delete_children 连锁）。副作用：不可逆", {"vm_id": {"type": "string"}, "name": {"type": "string"}, "delete_children": {"type": "boolean"}}, ["vm_id", "name"]),
        T("snapshot_clone", "vmcli｜从快照克隆 VM（linked/full）。长任务（600s 上限）。副作用：磁盘占用", {"vm_id": {"type": "string"}, "snapshot_name": {"type": "string"}, "dest_path": {"type": "string"}, "clone_type": {"type": "string", "enum": ["linked", "full"]}}, ["vm_id", "snapshot_name", "dest_path"]),
        # Guest
        T("guest_run", "vmcli｜guest 内执行程序（凭据经 user/password，加密密码本通道未支持，加密 VM 用 vmrun_run）。前提：运行+Tools+凭据。副作用：guest 内进程", {"vm_id": {"type": "string"}, "program": {"type": "string"}, "args": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "program"]),
        T("guest_ps", "vmcli｜列 guest 进程。与 vmrun_ps 重叠。只读", {"vm_id": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id"]),
        T("guest_kill", "vmcli｜杀 guest 进程。副作用：终止进程", {"vm_id": {"type": "string"}, "pid": {"type": "integer"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "pid"]),
        T("guest_ls", "vmcli｜列 guest 目录。与 vmrun_ls 重叠。只读", {"vm_id": {"type": "string"}, "path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "path"]),
        T("guest_mkdir", "vmcli｜建 guest 目录。副作用：guest 文件系统", {"vm_id": {"type": "string"}, "path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "path"]),
        T("guest_rm", "vmcli｜删 guest 文件。副作用：guest 文件系统", {"vm_id": {"type": "string"}, "path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "path"]),
        T("guest_rmdir", "vmcli｜删 guest 目录。副作用：guest 文件系统", {"vm_id": {"type": "string"}, "path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "path"]),
        T("guest_copy_to", "vmcli｜单文件 宿主→guest。与 vmrun_copy_to 重叠。副作用：guest 文件系统", {"vm_id": {"type": "string"}, "host_path": {"type": "string"}, "guest_path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "host_path", "guest_path"]),
        T("guest_copy_from", "vmcli｜单文件 guest→宿主。与 vmrun_copy_from 重叠。副作用：宿主文件系统", {"vm_id": {"type": "string"}, "guest_path": {"type": "string"}, "host_path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "guest_path", "host_path"]),
        T("guest_env", "vmcli｜读 guest 环境变量。前提：运行+Tools+凭据。只读", {"vm_id": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id"]),
        # MKS
        T("mks_screenshot", "vmcli｜MKS 截屏存宿主文件。与 vmrun_screenshot 重叠。VM 需运行", {"vm_id": {"type": "string"}, "output_path": {"type": "string"}}, ["vm_id", "output_path"]),
        T("mks_send_key", "vmcli｜发键序列到 VM 控制台。副作用：键盘输入", {"vm_id": {"type": "string"}, "key_sequence": {"type": "string"}}, ["vm_id", "key_sequence"]),
        T("mks_query", "vmcli｜查 MKS（显示）状态。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        # Chipset
        T("chipset_query", "vmcli｜查 CPU/内存配置。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("chipset_set_cpu", "vmcli｜设 vCPU 数。建议关机时改。副作用：改 VM 硬件", {"vm_id": {"type": "string"}, "count": {"type": "integer"}}, ["vm_id", "count"]),
        T("chipset_set_memory", "vmcli｜设内存 MB。建议关机时改。副作用：改 VM 硬件", {"vm_id": {"type": "string"}, "size_mb": {"type": "integer"}}, ["vm_id", "size_mb"]),
        T("chipset_set_cores", "vmcli｜设每槽核数。建议关机时改。副作用：改 VM 硬件", {"vm_id": {"type": "string"}, "cores": {"type": "integer"}}, ["vm_id", "cores"]),
        # Tools
        T("tools_query", "vmcli｜查 Tools 状态。与 vmrun_tools_state 重叠。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("tools_install", "vmcli｜安装 Tools（可能分钟级）。副作用：guest 内安装软件", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("tools_upgrade", "vmcli｜升级 Tools（长任务）。副作用：guest 内升级软件", {"vm_id": {"type": "string"}}, ["vm_id"]),
        # Template
        T("template_create", "vmcli｜VM 转模板。长任务。副作用：VM 从清单转为模板", {"vm_id": {"type": "string"}, "template_path": {"type": "string"}, "name": {"type": "string"}}, ["vm_id", "template_path", "name"]),
        T("template_deploy", "vmcli｜模板部署为新 VM（长任务 600s 上限）。副作用：创建新 VM", {"template_path": {"type": "string"}, "dest_path": {"type": "string"}, "name": {"type": "string"}}, ["template_path", "dest_path", "name"]),
        # Disk
        T("disk_query", "vmcli｜查磁盘配置。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("disk_create", "vmcli｜创建虚拟磁盘（size_gb + disk_type + adapter/device）。长任务。副作用：磁盘占用", {"vm_id": {"type": "string"}, "size_gb": {"type": "integer"}, "disk_type": {"type": "string"}, "adapter": {"type": "integer"}, "device": {"type": "integer"}}, ["vm_id", "size_gb"]),
        T("disk_extend", "vmcli｜扩容磁盘（长任务）。副作用：改磁盘容量，不可逆", {"vm_id": {"type": "string"}, "new_size_gb": {"type": "integer"}, "adapter": {"type": "integer"}, "device": {"type": "integer"}}, ["vm_id", "new_size_gb"]),
        # Config
        T("config_query", "vmcli｜查 vmx ConfigParams。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("config_set", "vmcli｜改 vmx ConfigParams 单项。副作用：改 VM 配置", {"vm_id": {"type": "string"}, "key": {"type": "string"}, "value": {"type": "string"}}, ["vm_id", "key", "value"]),
        # Power (vmcli)
        T("power_query", "vmcli｜查电源状态。与 vm_power_get/vmrun_list 重叠。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("power_start", "vmcli｜启动 VM。加密 VM 用 vmrun_start（enc_pass）。副作用：电源 on", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("power_stop", "vmcli｜关机。副作用：电源 off", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("power_pause", "vmcli｜暂停。副作用：暂停态", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("power_unpause", "vmcli｜恢复暂停。副作用：退出暂停态", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("power_reset", "vmcli｜重启。副作用：电源重启", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("power_suspend", "vmcli｜挂起。副作用：电源挂起", {"vm_id": {"type": "string"}}, ["vm_id"]),
        # Ethernet
        T("ethernet_query", "vmcli｜查网卡配置。与 vm_nic_list（REST）重叠。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("ethernet_set_type", "vmcli｜设网卡连接类型（按 index）。副作用：改 VM 网络硬件", {"vm_id": {"type": "string"}, "index": {"type": "integer"}, "type": {"type": "string", "enum": ["bridged", "nat", "hostonly", "custom"]}}, ["vm_id", "index", "type"]),
        T("ethernet_set_present", "vmcli｜网卡在位/移除（按 index）。副作用：改 VM 网络硬件", {"vm_id": {"type": "string"}, "index": {"type": "integer"}, "present": {"type": "boolean"}}, ["vm_id", "index", "present"]),
        T("ethernet_set_connected", "vmcli｜设开机自动连接。副作用：改 VM 配置", {"vm_id": {"type": "string"}, "index": {"type": "integer"}, "connected": {"type": "boolean"}}, ["vm_id", "index", "connected"]),
        T("ethernet_set_device", "vmcli｜设虚拟设备型号（按 index）。副作用：改 VM 网络硬件", {"vm_id": {"type": "string"}, "index": {"type": "integer"}, "device": {"type": "string"}}, ["vm_id", "index", "device"]),
        T("ethernet_set_network", "vmcli｜设网卡接入的网络名。副作用：改 VM 网络", {"vm_id": {"type": "string"}, "index": {"type": "integer"}, "name": {"type": "string"}}, ["vm_id", "index", "name"]),
        T("ethernet_purge", "vmcli｜移除网卡（按 index）。与 vm_nic_delete 重叠。副作用：改 VM 网络硬件", {"vm_id": {"type": "string"}, "index": {"type": "integer"}}, ["vm_id", "index"]),
        # HGFS
        T("hgfs_query", "vmcli｜查 HGFS 共享目录。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("hgfs_set_enabled", "vmcli｜启/禁 HGFS（按 index）。副作用：VM 共享配置", {"vm_id": {"type": "string"}, "index": {"type": "integer"}, "enabled": {"type": "boolean"}}, ["vm_id", "index", "enabled"]),
        T("hgfs_set_path", "vmcli｜设共享目录宿主路径。副作用：VM 共享配置", {"vm_id": {"type": "string"}, "index": {"type": "integer"}, "path": {"type": "string"}}, ["vm_id", "index", "path"]),
        T("hgfs_set_name", "vmcli｜设共享目录 guest 名。副作用：VM 共享配置", {"vm_id": {"type": "string"}, "index": {"type": "integer"}, "name": {"type": "string"}}, ["vm_id", "index", "name"]),
        T("hgfs_set_read", "vmcli｜设读权限。副作用：VM 共享配置", {"vm_id": {"type": "string"}, "index": {"type": "integer"}, "read": {"type": "boolean"}}, ["vm_id", "index", "read"]),
        T("hgfs_set_write", "vmcli｜设写权限。副作用：VM 共享配置", {"vm_id": {"type": "string"}, "index": {"type": "integer"}, "write": {"type": "boolean"}}, ["vm_id", "index", "write"]),
        # Serial
        T("serial_query", "vmcli｜查串口配置。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("serial_set_present", "vmcli｜串口在位/移除（按 index）。副作用：改 VM 硬件", {"vm_id": {"type": "string"}, "index": {"type": "integer"}, "present": {"type": "boolean"}}, ["vm_id", "index", "present"]),
        T("serial_purge", "vmcli｜移除串口。副作用：改 VM 硬件", {"vm_id": {"type": "string"}, "index": {"type": "integer"}}, ["vm_id", "index"]),
        # Sata
        T("sata_query", "vmcli｜查 SATA 控制器。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("sata_set_present", "vmcli｜SATA 控制器在位/移除（按 adapter）。副作用：改 VM 硬件", {"vm_id": {"type": "string"}, "adapter": {"type": "integer"}, "present": {"type": "boolean"}}, ["vm_id", "adapter", "present"]),
        T("sata_purge", "vmcli｜移除 SATA 控制器。副作用：改 VM 硬件", {"vm_id": {"type": "string"}, "adapter": {"type": "integer"}}, ["vm_id", "adapter"]),
        # Nvme
        T("nvme_query", "vmcli｜查 NVMe 控制器。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("nvme_set_present", "vmcli｜NVMe 控制器在位/移除（按 adapter）。副作用：改 VM 硬件", {"vm_id": {"type": "string"}, "adapter": {"type": "integer"}, "present": {"type": "boolean"}}, ["vm_id", "adapter", "present"]),
        T("nvme_purge", "vmcli｜移除 NVMe 控制器。副作用：改 VM 硬件", {"vm_id": {"type": "string"}, "adapter": {"type": "integer"}}, ["vm_id", "adapter"]),
        # VProbes
        T("vprobes_query", "vmcli｜查 VProbes 状态（调试探针）。只读", {"vm_id": {"type": "string"}}, ["vm_id"]),
        T("vprobes_enable", "vmcli｜启/禁 VProbes。副作用：改调试探针状态", {"vm_id": {"type": "string"}, "enabled": {"type": "boolean"}}, ["vm_id", "enabled"]),
        T("vprobes_load", "vmcli｜装载 VProbes 脚本。副作用：注入探针", {"vm_id": {"type": "string"}, "script_path": {"type": "string"}}, ["vm_id", "script_path"]),
        T("vprobes_reset", "vmcli｜复位 VProbes。副作用：清除探针", {"vm_id": {"type": "string"}}, ["vm_id"]),
    ]

    # vmrun 系（带 vm_id 的）工具统一注入可选 enc_pass 参数（加密 VM 密码入口）
    enc_schema = {"type": "string", "description": "加密 VM 的密码（等效 vmrun -vp）；也可用 env VMWARE_ENC_PASSWORD 或 set_vm_encryption_password 预存"}
    # 所有 vm_id 参数统一写明双语义（vmx 绝对路径或 REST vm_id）
    vm_id_desc = "vmx 绝对路径（如 D:\\vms\\win11.vmx）或 REST vm_id（vm_list 获取），服务端自动解析"
    confirm_schema = {"type": "boolean", "description": "破坏性操作确认：缺省时仅返回 dry-run 预览不执行；传 true 才实际执行。VMWARE_READ_ONLY=1 时无条件拒绝"}
    for tool in tools:
        props = tool.inputSchema["properties"]
        if tool.name.startswith("vmrun_") and "vm_id" in props and "enc_pass" not in props:
            props["enc_pass"] = dict(enc_schema)
        if "vm_id" in props and "description" not in props["vm_id"]:
            props["vm_id"] = {**props["vm_id"], "description": vm_id_desc}
        # annotations 行为标注（宿主可据此决定是否自动批准）
        hints = {}
        if tool.name in READ_ONLY_TOOLS:
            hints["readOnlyHint"] = True
        if tool.name in DESTRUCTIVE_TOOLS:
            hints["readOnlyHint"] = False
            hints["destructiveHint"] = True
        if hints:
            tool.annotations = ToolAnnotations(**hints)
        # 破坏性工具注入可选 confirm 参数（零破坏：可选，不改既有参数）
        if tool.name in DESTRUCTIVE_TOOLS or tool.name == "vm_power_set":
            props["confirm"] = dict(confirm_schema)
    return tools


@server.list_tools()
async def list_tools() -> list[Tool]:
    global _TOOLS_CACHE
    if _TOOLS_CACHE is None:
        _TOOLS_CACHE = _build_tools()
    return _TOOLS_CACHE


@server.call_tool()
@_structured
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    client = get_client()
    vmcli = get_vmcli()
    vmrun = get_vmrun()
    result = _UNHANDLED
    a = arguments

    # 护栏：破坏性工具三层拦截（顺序：全局只读 → confirm 确认 → 放行）
    if _is_destructive(name, a):
        if _read_only_mode():
            raise ToolError(
                f"read-only mode: destructive tool '{name}' refused",
                read_only=True,
                hint="服务以 VMWARE_READ_ONLY=1 启动，一切破坏性操作被拒；去除该环境变量并重启服务后可用",
            )
        if not a.get("confirm"):
            return _error_content({
                "ok": False,
                "dry_run": True,
                "tool": name,
                "arguments": {k: v for k, v in a.items() if k != "enc_pass"},
                "note": "DRY-RUN：以上操作未执行",
                "hint": f"确认无误后，携带 confirm: true 再次调用以实际执行 {name}",
            })

    # Helper：解析 vm_id，并为本次调用写入加密密码（显式 enc_pass > set 工具预存 > env，env 兜底在 vmx 内完成）
    async def vmx(vm_id: str) -> str:
        path = await get_vmx_path(vm_id)
        enc_password.set(a.get("enc_pass", "") or _enc_passwords.get(path, "") or os.getenv("VMWARE_ENC_PASSWORD", ""))
        return path

    # ==================== SERVER ====================
    if name == "set_vm_encryption_password":
        try:
            key = await get_vmx_path(a["vm_id"])
        except ToolError:
            key = a["vm_id"]
        _enc_passwords[key] = a["password"]
        result = {"status": "stored", "vm_id": key, "note": "密码仅存于服务进程内存，进程重启后失效；持久方案用 env VMWARE_ENC_PASSWORD"}
    elif name == "vm_resolve":
        query = str(a["query"]).lower()
        vms = await client.list_vms()
        for vm in vms:
            _vm_path_cache[vm["id"]] = vm["path"]
        resolved = []
        for vm in vms:
            if query in str(vm.get("path", "")).lower() or query in str(vm.get("id", "")).lower():
                entry = {"id": vm.get("id"), "path": vm.get("path"), "encryptionType": _vmx_encryption(vm.get("path", ""))}
                try:
                    entry["power"] = await client.get_power_state(vm["id"])
                except Exception as e:
                    entry["power"] = f"unavailable: {type(e).__name__}: {e}"
                resolved.append(entry)
        result = {"query": a["query"], "count": len(resolved), "matches": resolved}
    elif name == "vm_health":
        vmx_path = await vmx(a["vm_id"])
        vmdir = os.path.dirname(vmx_path) or "."
        health = {"vm_id": a["vm_id"], "vmx": vmx_path, "encryptionType": _vmx_encryption(vmx_path)}
        try:
            listing = await vmrun.list_running()
            health["running"] = vmx_path.lower() in listing.lower()
        except ToolError as e:
            health["running"] = f"unknown: {e}"
        try:
            health["tools"] = await vmrun.check_tools_state(vmx_path)
        except ToolError as e:
            health["tools"] = f"error: {e}"
        try:
            health["ip"] = await vmrun.get_guest_ip(vmx_path)
        except ToolError:
            health["ip"] = None
        vmem = glob.glob(os.path.join(vmdir, "*.vmem"))
        vmss = glob.glob(os.path.join(vmdir, "*.vmss"))
        health["suspend_artifacts"] = {"vmem": vmem, "vmss": vmss}
        if vmem and vmss:
            health["hint"] = "存在 .vmem/.vmss：VM 多半被空闲挂起（非关机），vmrun_start 可从挂起点无损恢复"
        health["log_tail"] = _log_tail(os.path.join(vmdir, "vmware.log"), 20)
        result = health
    elif name == "vm_log_tail":
        vmx_path = await vmx(a["vm_id"])
        result = {"log": _log_tail(os.path.join(os.path.dirname(vmx_path) or ".", "vmware.log"), int(a.get("lines", 50)))}
    elif name == "screenshot_ocr":
        image = a["output_path"]
        await vmrun.capture_screen(await vmx(a["vm_id"]), image)
        result = _ocr_image(image)
    # ==================== REST API ====================
    if name == "vm_list":
        result = await client.list_vms()
        for vm in result:
            _vm_path_cache[vm["id"]] = vm["path"]
    elif name == "vm_get":
        result = await client.get_vm(a["vm_id"])
    elif name == "vm_create":
        result = await client.create_vm(a["vm_id"], a["name"])
    elif name == "vm_delete":
        await client.delete_vm(a["vm_id"])
        result = {"status": "deleted"}
    elif name == "vm_update":
        settings = {k: v for k, v in a.items() if k != "vm_id" and v is not None}
        result = await client.update_vm(a["vm_id"], settings)
    elif name == "vm_power_get":
        result = await client.get_power_state(a["vm_id"])
    elif name == "vm_power_set":
        result = await client.change_power_state(a["vm_id"], a["state"])
    elif name == "vm_nic_list":
        result = await client.list_nics(a["vm_id"])
    elif name == "vm_nic_create":
        result = await client.create_nic(a["vm_id"], {"type": a["type"]})
    elif name == "vm_nic_delete":
        await client.delete_nic(a["vm_id"], a["index"])
        result = {"status": "deleted"}
    elif name == "vm_ip_get":
        result = await client.get_vm_ip(a["vm_id"])
    elif name == "vm_folder_list":
        result = await client.list_shared_folders(a["vm_id"])
    elif name == "vm_folder_create":
        result = await client.create_shared_folder(a["vm_id"], {"folder_id": a["folder_id"], "host_path": a["host_path"], "flags": a.get("flags", 0)})
    elif name == "vm_folder_delete":
        await client.delete_shared_folder(a["vm_id"], a["folder_id"])
        result = {"status": "deleted"}
    elif name == "network_list":
        result = await client.list_networks()
    elif name == "network_create":
        result = await client.create_network({"name": a["name"], "type": a["type"]})
    elif name == "network_portforward_list":
        result = await client.get_portforwards(a["vmnet"])
    elif name == "network_portforward_set":
        result = await client.update_portforward(a["vmnet"], a["protocol"], a["port"], {"guestIp": a["guest_ip"], "guestPort": a["guest_port"]})
    elif name == "network_portforward_delete":
        await client.delete_portforward(a["vmnet"], a["protocol"], a["port"])
        result = {"status": "deleted"}

    # ==================== VMRUN ====================
    elif name == "vmrun_list":
        result = await vmrun.list_running()
    elif name == "vmrun_clone":
        result = await vmrun.clone(await vmx(a["vm_id"]), a["dest_path"], a.get("clone_type", "linked"), a.get("snapshot", ""), a.get("clone_name", ""))
    elif name == "vmrun_upgrade":
        result = await vmrun.upgrade_vm(await vmx(a["vm_id"]))
    elif name == "vmrun_delete":
        result = await vmrun.delete_vm(await vmx(a["vm_id"]))
    elif name == "vmrun_start":
        result = await vmrun.start(await vmx(a["vm_id"]), a.get("gui", True))
    elif name == "vmrun_stop":
        result = await vmrun.stop(await vmx(a["vm_id"]), a.get("hard", False))
    elif name == "vmrun_reset":
        result = await vmrun.reset(await vmx(a["vm_id"]), a.get("hard", False))
    elif name == "vmrun_suspend":
        result = await vmrun.suspend(await vmx(a["vm_id"]), a.get("hard", False))
    elif name == "vmrun_pause":
        result = await vmrun.pause(await vmx(a["vm_id"]))
    elif name == "vmrun_unpause":
        result = await vmrun.unpause(await vmx(a["vm_id"]))
    elif name == "vmrun_snapshot_list":
        result = await vmrun.list_snapshots(await vmx(a["vm_id"]), a.get("show_tree", False))
    elif name == "vmrun_snapshot_take":
        result = await vmrun.snapshot(await vmx(a["vm_id"]), a["name"])
    elif name == "vmrun_snapshot_delete":
        result = await vmrun.delete_snapshot(await vmx(a["vm_id"]), a["name"], a.get("delete_children", False))
    elif name == "vmrun_snapshot_revert":
        result = await vmrun.revert_to_snapshot(await vmx(a["vm_id"]), a["name"])
    elif name == "vmrun_file_exists":
        result = await vmrun.file_exists(await vmx(a["vm_id"]), a["path"], a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_dir_exists":
        result = await vmrun.directory_exists(await vmx(a["vm_id"]), a["path"], a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_ls":
        result = await vmrun.list_directory(await vmx(a["vm_id"]), a["path"], a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_mkdir":
        result = await vmrun.create_directory(await vmx(a["vm_id"]), a["path"], a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_rmdir":
        result = await vmrun.delete_directory(await vmx(a["vm_id"]), a["path"], a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_rm":
        result = await vmrun.delete_file(await vmx(a["vm_id"]), a["path"], a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_rename":
        result = await vmrun.rename_file(await vmx(a["vm_id"]), a["old_path"], a["new_path"], a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_copy_to":
        result = await vmrun.copy_to_guest(await vmx(a["vm_id"]), a["host_path"], a["guest_path"], a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_copy_from":
        result = await vmrun.copy_from_guest(await vmx(a["vm_id"]), a["guest_path"], a["host_path"], a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_temp_file":
        result = await vmrun.create_temp_file(await vmx(a["vm_id"]), a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_copy_dir_to":
        result = await vmrun.copy_dir_to(await vmx(a["vm_id"]), a["host_path"], a["guest_path"], a.get("include", ""), a.get("exclude", ""), a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_copy_dir_from":
        result = await vmrun.copy_dir_from(await vmx(a["vm_id"]), a["guest_path"], a["host_path"], a.get("include", ""), a.get("exclude", ""), a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_run":
        result = await vmrun.run_program(await vmx(a["vm_id"]), a["program"], a.get("args", ""), a.get("no_wait", False), False, a.get("interactive", False), a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_script":
        result = await vmrun.run_script(await vmx(a["vm_id"]), a["interpreter"], a["script"], a.get("no_wait", False), False, False, a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_ps":
        result = await vmrun.list_processes(await vmx(a["vm_id"]), a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_kill":
        result = await vmrun.kill_process(await vmx(a["vm_id"]), a["pid"], a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_shared_enable":
        result = await vmrun.enable_shared_folders(await vmx(a["vm_id"]))
    elif name == "vmrun_shared_disable":
        result = await vmrun.disable_shared_folders(await vmx(a["vm_id"]))
    elif name == "vmrun_shared_add":
        result = await vmrun.add_shared_folder(await vmx(a["vm_id"]), a["name"], a["host_path"])
    elif name == "vmrun_shared_remove":
        result = await vmrun.remove_shared_folder(await vmx(a["vm_id"]), a["name"])
    elif name == "vmrun_shared_set":
        result = await vmrun.set_shared_folder_state(await vmx(a["vm_id"]), a["name"], a["host_path"], a.get("writable", True))
    elif name == "vmrun_device_connect":
        result = await vmrun.connect_device(await vmx(a["vm_id"]), a["device"])
    elif name == "vmrun_device_disconnect":
        result = await vmrun.disconnect_device(await vmx(a["vm_id"]), a["device"])
    elif name == "vmrun_var_read":
        result = await vmrun.read_variable(await vmx(a["vm_id"]), a["var_type"], a["name"], a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_var_write":
        result = await vmrun.write_variable(await vmx(a["vm_id"]), a["var_type"], a["name"], a["value"], a.get("user", ""), a.get("password", ""))
    elif name == "vmrun_screenshot":
        result = await vmrun.capture_screen(await vmx(a["vm_id"]), a["output_path"])
    elif name == "vmrun_keystrokes":
        result = await vmrun.type_keystrokes(await vmx(a["vm_id"]), a["keystrokes"])
    elif name == "vmrun_tools_install":
        result = await vmrun.install_tools(await vmx(a["vm_id"]))
    elif name == "vmrun_tools_state":
        result = await vmrun.check_tools_state(await vmx(a["vm_id"]))
    elif name == "vmrun_guest_ip":
        result = await vmrun.get_guest_ip(await vmx(a["vm_id"]), a.get("wait", False))
    elif name == "vmrun_host_networks":
        result = await vmrun.list_host_networks()
    elif name == "vmrun_portforward_list":
        result = await vmrun.list_port_forwardings(a["network"])
    elif name == "vmrun_portforward_set":
        result = await vmrun.set_port_forwarding(a["network"], a["protocol"], a["host_port"], a["guest_ip"], a["guest_port"], a.get("description", ""))
    elif name == "vmrun_portforward_delete":
        result = await vmrun.delete_port_forwarding(a["network"], a["protocol"], a["host_port"])

    # ==================== VMCLI ====================
    elif name == "snapshot_list":
        result = await vmcli.snapshot_list(await vmx(a["vm_id"]))
    elif name == "snapshot_take":
        result = await vmcli.snapshot_take(await vmx(a["vm_id"]), a["name"])
    elif name == "snapshot_revert":
        result = await vmcli.snapshot_revert(await vmx(a["vm_id"]), a["name"])
    elif name == "snapshot_delete":
        result = await vmcli.snapshot_delete(await vmx(a["vm_id"]), a["name"], a.get("delete_children", False))
    elif name == "snapshot_clone":
        result = await vmcli.snapshot_clone(await vmx(a["vm_id"]), a["snapshot_name"], a["dest_path"], a.get("clone_type", "linked"))
    elif name == "guest_run":
        result = await vmcli.guest_run(await vmx(a["vm_id"]), a["program"], a.get("args", ""), a.get("user", ""), a.get("password", ""))
    elif name == "guest_ps":
        result = await vmcli.guest_ps(await vmx(a["vm_id"]), a.get("user", ""), a.get("password", ""))
    elif name == "guest_kill":
        result = await vmcli.guest_kill(await vmx(a["vm_id"]), a["pid"], a.get("user", ""), a.get("password", ""))
    elif name == "guest_ls":
        result = await vmcli.guest_ls(await vmx(a["vm_id"]), a["path"], a.get("user", ""), a.get("password", ""))
    elif name == "guest_mkdir":
        result = await vmcli.guest_mkdir(await vmx(a["vm_id"]), a["path"], a.get("user", ""), a.get("password", ""))
    elif name == "guest_rm":
        result = await vmcli.guest_rm(await vmx(a["vm_id"]), a["path"], a.get("user", ""), a.get("password", ""))
    elif name == "guest_rmdir":
        result = await vmcli.guest_rmdir(await vmx(a["vm_id"]), a["path"], a.get("user", ""), a.get("password", ""))
    elif name == "guest_copy_to":
        result = await vmcli.guest_copy_to(await vmx(a["vm_id"]), a["host_path"], a["guest_path"], a.get("user", ""), a.get("password", ""))
    elif name == "guest_copy_from":
        result = await vmcli.guest_copy_from(await vmx(a["vm_id"]), a["guest_path"], a["host_path"], a.get("user", ""), a.get("password", ""))
    elif name == "guest_env":
        result = await vmcli.guest_env(await vmx(a["vm_id"]), a.get("user", ""), a.get("password", ""))
    elif name == "mks_screenshot":
        result = await vmcli.mks_screenshot(await vmx(a["vm_id"]), a["output_path"])
    elif name == "mks_send_key":
        result = await vmcli.mks_send_key(await vmx(a["vm_id"]), a["key_sequence"])
    elif name == "mks_query":
        result = await vmcli.mks_query(await vmx(a["vm_id"]))
    elif name == "chipset_query":
        result = await vmcli.chipset_query(await vmx(a["vm_id"]))
    elif name == "chipset_set_cpu":
        result = await vmcli.chipset_set_cpu(await vmx(a["vm_id"]), a["count"])
    elif name == "chipset_set_memory":
        result = await vmcli.chipset_set_memory(await vmx(a["vm_id"]), a["size_mb"])
    elif name == "chipset_set_cores":
        result = await vmcli.chipset_set_cores_per_socket(await vmx(a["vm_id"]), a["cores"])
    elif name == "tools_query":
        result = await vmcli.tools_query(await vmx(a["vm_id"]))
    elif name == "tools_install":
        result = await vmcli.tools_install(await vmx(a["vm_id"]))
    elif name == "tools_upgrade":
        result = await vmcli.tools_upgrade(await vmx(a["vm_id"]))
    elif name == "template_create":
        result = await vmcli.template_create(await vmx(a["vm_id"]), a["template_path"], a["name"])
    elif name == "template_deploy":
        result = await vmcli.template_deploy(a["template_path"], a["dest_path"], a["name"])
    elif name == "disk_query":
        result = await vmcli.disk_query(await vmx(a["vm_id"]))
    elif name == "disk_create":
        result = await vmcli.disk_create(await vmx(a["vm_id"]), a["size_gb"], a.get("disk_type", "scsi"), a.get("adapter", 0), a.get("device", 0))
    elif name == "disk_extend":
        result = await vmcli.disk_extend(await vmx(a["vm_id"]), a["new_size_gb"], a.get("adapter", 0), a.get("device", 0))
    elif name == "config_query":
        result = await vmcli.config_query(await vmx(a["vm_id"]))
    elif name == "config_set":
        result = await vmcli.config_set(await vmx(a["vm_id"]), a["key"], a["value"])
    elif name == "power_query":
        result = await vmcli.power_query(await vmx(a["vm_id"]))
    elif name == "power_start":
        result = await vmcli.power_start(await vmx(a["vm_id"]))
    elif name == "power_stop":
        result = await vmcli.power_stop(await vmx(a["vm_id"]))
    elif name == "power_pause":
        result = await vmcli.power_pause(await vmx(a["vm_id"]))
    elif name == "power_unpause":
        result = await vmcli.power_unpause(await vmx(a["vm_id"]))
    elif name == "power_reset":
        result = await vmcli.power_reset(await vmx(a["vm_id"]))
    elif name == "power_suspend":
        result = await vmcli.power_suspend(await vmx(a["vm_id"]))
    elif name == "ethernet_query":
        result = await vmcli.ethernet_query(await vmx(a["vm_id"]))
    elif name == "ethernet_set_type":
        result = await vmcli.ethernet_set_connection_type(await vmx(a["vm_id"]), a["index"], a["type"])
    elif name == "ethernet_set_present":
        result = await vmcli.ethernet_set_present(await vmx(a["vm_id"]), a["index"], a["present"])
    elif name == "ethernet_set_connected":
        result = await vmcli.ethernet_set_start_connected(await vmx(a["vm_id"]), a["index"], a["connected"])
    elif name == "ethernet_set_device":
        result = await vmcli.ethernet_set_virtual_device(await vmx(a["vm_id"]), a["index"], a["device"])
    elif name == "ethernet_set_network":
        result = await vmcli.ethernet_set_network_name(await vmx(a["vm_id"]), a["index"], a["name"])
    elif name == "ethernet_purge":
        result = await vmcli.ethernet_purge(await vmx(a["vm_id"]), a["index"])
    elif name == "hgfs_query":
        result = await vmcli.hgfs_query(await vmx(a["vm_id"]))
    elif name == "hgfs_set_enabled":
        result = await vmcli.hgfs_set_enabled(await vmx(a["vm_id"]), a["index"], a["enabled"])
    elif name == "hgfs_set_path":
        result = await vmcli.hgfs_set_host_path(await vmx(a["vm_id"]), a["index"], a["path"])
    elif name == "hgfs_set_name":
        result = await vmcli.hgfs_set_guest_name(await vmx(a["vm_id"]), a["index"], a["name"])
    elif name == "hgfs_set_read":
        result = await vmcli.hgfs_set_read_access(await vmx(a["vm_id"]), a["index"], a["read"])
    elif name == "hgfs_set_write":
        result = await vmcli.hgfs_set_write_access(await vmx(a["vm_id"]), a["index"], a["write"])
    elif name == "serial_query":
        result = await vmcli.serial_query(await vmx(a["vm_id"]))
    elif name == "serial_set_present":
        result = await vmcli.serial_set_present(await vmx(a["vm_id"]), a["index"], a["present"])
    elif name == "serial_purge":
        result = await vmcli.serial_purge(await vmx(a["vm_id"]), a["index"])
    elif name == "sata_query":
        result = await vmcli.sata_query(await vmx(a["vm_id"]))
    elif name == "sata_set_present":
        result = await vmcli.sata_set_present(await vmx(a["vm_id"]), a["adapter"], a["present"])
    elif name == "sata_purge":
        result = await vmcli.sata_purge(await vmx(a["vm_id"]), a["adapter"])
    elif name == "nvme_query":
        result = await vmcli.nvme_query(await vmx(a["vm_id"]))
    elif name == "nvme_set_present":
        result = await vmcli.nvme_set_present(await vmx(a["vm_id"]), a["adapter"], a["present"])
    elif name == "nvme_purge":
        result = await vmcli.nvme_purge(await vmx(a["vm_id"]), a["adapter"])
    elif name == "vprobes_query":
        result = await vmcli.vprobes_query(await vmx(a["vm_id"]))
    elif name == "vprobes_enable":
        result = await vmcli.vprobes_set_enabled(await vmx(a["vm_id"]), a["enabled"])
    elif name == "vprobes_load":
        result = await vmcli.vprobes_load(await vmx(a["vm_id"]), a["script_path"])
    elif name == "vprobes_reset":
        result = await vmcli.vprobes_reset(await vmx(a["vm_id"]))

    if result is _UNHANDLED:
        raise ToolError(f"Unknown tool: {name}", tool=name, hint="工具名不存在；以 list_tools 返回为准")
    if isinstance(result, str):
        return [TextContent(type="text", text=_truncate_output(result) if result else "OK")]
    return [TextContent(type="text", text=_truncate_output(json.dumps(result, indent=2, ensure_ascii=False)) if result else "OK")]


def _setup_logging() -> None:
    """stdout 只跑协议；一切人类可读日志走 stderr。默认 WARNING（基本静默），INFO 起输出每次调用的耗时行。"""
    logging.basicConfig(
        level=getattr(logging, os.getenv("VMWARE_LOG_LEVEL", "WARNING").strip().upper(), logging.WARNING),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main():
    import asyncio

    _setup_logging()

    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(run())


if __name__ == "__main__":
    main()
