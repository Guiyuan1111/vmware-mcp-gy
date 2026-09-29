"""VMware MCP Server - Complete implementation with REST API, vmcli, and vmrun."""

import asyncio
import glob
import json
import logging
import os
import secrets
import sys
import tempfile
import time

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ToolAnnotations

from .client import VMwareClient
from .errors import ToolError, make_hint
from .runtime import decode_output, enc_password, env_int
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
    "vmrun_read_file", "vmrun_wait_file",
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
    """读 vmx 文件中的 encryptionType；无该键返回 none，文件不可读返回 unknown。

    注：0.3.1 曾试过"整读+正则定位"，基准（benchmark/bench_hotpath.py）显示慢于逐行
    （文件 I/O ~100µs 主导，encryptionType 通常在前几十行、逐行提前命中即停），已回滚。
    """
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
    """构建全部 140 个工具定义（含 enc_pass/vm_id/confirm 注入与 annotations）。
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
        T("vmrun_copy_from", "vmrun｜单文件 guest→宿主。只要文本内容时改用 vmrun_read_file（一次调用直达对话）。前提：运行+Tools+凭据。整目录用 vmrun_copy_dir_from", {"vm_id": {"type": "string"}, "guest_path": {"type": "string"}, "host_path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "guest_path", "host_path"]),
        T("vmrun_read_file", "vmrun｜直读 guest 文本文件内容到对话（合并 copy_from+本地 Read 为一次调用）。二进制/超大文件用 vmrun_copy_from。只读（宿主临时中转文件即用即删）。前提：运行+Tools+凭据", {"vm_id": {"type": "string"}, "path": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "path"]),
        T("vmrun_temp_file", "vmrun｜在 guest 内建临时文件。前提：运行+Tools+凭据。副作用：guest 文件系统", {"vm_id": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id"]),
        T("vmrun_copy_dir_to", "vmrun｜递归复制宿主目录树→guest（多于 3 个文件时优先用我，一次调用替代逐文件 copy_to 循环；include/exclude 传逗号分隔后缀如 \".txt,.log\"；自动逐级建父目录）。前提：运行+Tools+凭据。副作用：guest 文件系统", {"vm_id": {"type": "string"}, "host_path": {"type": "string"}, "guest_path": {"type": "string"}, "include": {"type": "string"}, "exclude": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "host_path", "guest_path"]),
        T("vmrun_copy_dir_from", "vmrun｜递归复制 guest 目录树→宿主（多于 3 个文件时优先用我，一次调用替代逐文件 copy_from 循环；基于 vmrun ls 容错解析，解析失败行计入 skipped）。前提：运行+Tools+凭据。副作用：宿主文件系统", {"vm_id": {"type": "string"}, "guest_path": {"type": "string"}, "host_path": {"type": "string"}, "include": {"type": "string"}, "exclude": {"type": "string"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "guest_path", "host_path"]),
        # Guest Process
        T("vmrun_run", "vmrun｜guest 内执行程序。args 传数组（每项一个参数，推荐）或整串（作为单个参数透传，不再按空格拆分）。注意：不捕获程序 stdout（仅返回 vmrun 自身状态）；需要程序输出/退出码改用 vmrun_run_job。前提：运行+Tools+凭据。副作用：guest 内进程", {"vm_id": {"type": "string"}, "program": {"type": "string"}, "args": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}, "no_wait": {"type": "boolean"}, "interactive": {"type": "boolean"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "program"]),
        T("vmrun_script", "vmrun｜guest 内执行脚本。script 是 guest 内脚本文件路径（不是脚本文本；内联脚本文本用 vmrun_run_job）。注意：不捕获 stdout；需要输出/退出码改用 vmrun_run_job。前提：运行+Tools+凭据。副作用：guest 内进程", {"vm_id": {"type": "string"}, "interpreter": {"type": "string"}, "script": {"type": "string"}, "no_wait": {"type": "boolean"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "interpreter", "script"]),
        T("vmrun_run_job", "vmrun｜guest 一键作业：上传脚本文本→执行→回传 stdout+exit_code 并清理 guest 临时文件（合并 copy_to+run+copy_from 三连为一次调用，省 2 个模型回合）。interpreter 支持 bash/sh 与 cmd/cmd.exe；guest_dir 为临时目录（POSIX 默认 /tmp，Windows 默认 C:\\Windows\\Temp）。长任务 no_wait=true 只启动不收集，之后 vmrun_wait_file+vmrun_read_file 收结果。前提：运行+Tools+凭据。副作用：guest 内进程与临时文件", {"vm_id": {"type": "string"}, "script": {"type": "string"}, "interpreter": {"type": "string", "enum": ["bash", "sh", "cmd", "cmd.exe"]}, "guest_dir": {"type": "string"}, "no_wait": {"type": "boolean"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "script"]),
        T("vmrun_wait_file", "vmrun｜轮询等待 guest 文件出现（收 no_wait 作业产物）。timeout_s 默认 25、上限 600（ZCode 客户端 30s 掐断调用，长等待请分次调用）。只读。前提：运行+Tools+凭据", {"vm_id": {"type": "string"}, "path": {"type": "string"}, "timeout_s": {"type": "number"}, "interval_s": {"type": "number"}, "user": {"type": "string"}, "password": {"type": "string"}}, ["vm_id", "path"]),
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


# ==================== 分发路由表（v0.3.1：if/elif 链 → O(1) 查表） ====================
# 行为契约：与原 if/elif 链逐分支等价，由 benchmark/verify_dispatch_equivalence.py 黄金快照保证。
# uniform 路由：(适配器, 方法名, 参数提取器序列, 固定返回值或 None)；提取器按原分支实参顺序求值。

def _e_vmx(key):
    async def e(a, vmx):
        return await vmx(a[key])
    return e


def _e_req(key):
    async def e(a, vmx):
        return a[key]
    return e


def _e_opt(key, default):
    async def e(a, vmx):
        return a.get(key, default)
    return e


def _e_cst(value):
    async def e(a, vmx):
        return value
    return e


_U = _e_vmx("vm_id")          # await vmx(a["vm_id"])
_R = _e_req
_O = _e_opt
_C = _e_cst
_UP = (_O("user", ""), _O("password", ""))   # guest 凭据可选项（大多数 vmrun/vmcli 工具尾部）
_DELETED = {"status": "deleted"}


# ---- 自定义逻辑分支（非纯透传，保持原实现） ----

async def _h_set_vm_encryption_password(a, vmx):
    try:
        key = await get_vmx_path(a["vm_id"])
    except ToolError:
        key = a["vm_id"]
    _enc_passwords[key] = a["password"]
    return {"status": "stored", "vm_id": key, "note": "密码仅存于服务进程内存，进程重启后失效；持久方案用 env VMWARE_ENC_PASSWORD"}


async def _h_vm_resolve(a, vmx):
    client = get_client()
    query = str(a["query"]).lower()
    vms = await client.list_vms()
    for vm in vms:
        _vm_path_cache[vm["id"]] = vm["path"]
    matches = [vm for vm in vms if query in str(vm.get("path", "")).lower() or query in str(vm.get("id", "")).lower()]
    # 多匹配的电源查询互不依赖，并发执行（gather 保序，结果与串行版逐字段一致）
    powers = await asyncio.gather(*(_resolve_power(client, vm["id"]) for vm in matches))
    resolved = [
        {"id": vm.get("id"), "path": vm.get("path"), "encryptionType": _vmx_encryption(vm.get("path", "")), "power": p}
        for vm, p in zip(matches, powers)
    ]
    return {"query": a["query"], "count": len(resolved), "matches": resolved}


async def _resolve_power(client, vm_id):
    try:
        return await client.get_power_state(vm_id)
    except Exception as e:
        return f"unavailable: {type(e).__name__}: {e}"


async def _health_running(vmrun, vmx_path):
    try:
        listing = await vmrun.list_running()
        return vmx_path.lower() in listing.lower()
    except ToolError as e:
        return f"unknown: {e}"


async def _health_tools(vmrun, vmx_path):
    try:
        return await vmrun.check_tools_state(vmx_path)
    except ToolError as e:
        return f"error: {e}"


async def _health_ip(vmrun, vmx_path):
    try:
        return await vmrun.get_guest_ip(vmx_path)
    except ToolError:
        return None


async def _h_vm_health(a, vmx):
    vmrun = get_vmrun()
    vmx_path = await vmx(a["vm_id"])
    vmdir = os.path.dirname(vmx_path) or "."
    health = {"vm_id": a["vm_id"], "vmx": vmx_path, "encryptionType": _vmx_encryption(vmx_path)}
    # 三个只读探测互不依赖，并发执行（并发上限仍由 vmrun._run 的全局信号量约束）；异常语义与原串行版一致
    health["running"], health["tools"], health["ip"] = await asyncio.gather(
        _health_running(vmrun, vmx_path), _health_tools(vmrun, vmx_path), _health_ip(vmrun, vmx_path),
    )
    vmem = glob.glob(os.path.join(vmdir, "*.vmem"))
    vmss = glob.glob(os.path.join(vmdir, "*.vmss"))
    health["suspend_artifacts"] = {"vmem": vmem, "vmss": vmss}
    if vmem and vmss:
        health["hint"] = "存在 .vmem/.vmss：VM 多半被空闲挂起（非关机），vmrun_start 可从挂起点无损恢复"
    health["log_tail"] = _log_tail(os.path.join(vmdir, "vmware.log"), 20)
    return health


async def _h_vm_log_tail(a, vmx):
    vmx_path = await vmx(a["vm_id"])
    return {"log": _log_tail(os.path.join(os.path.dirname(vmx_path) or ".", "vmware.log"), int(a.get("lines", 50)))}


async def _h_screenshot_ocr(a, vmx):
    image = a["output_path"]
    await get_vmrun().capture_screen(await vmx(a["vm_id"]), image)
    return _ocr_image(image)


async def _h_vm_list(a, vmx):
    result = await get_client().list_vms()
    for vm in result:
        _vm_path_cache[vm["id"]] = vm["path"]
    return result


async def _h_vm_create(a, vmx):
    return await get_client().create_vm(a["vm_id"], a["name"])


async def _h_vm_update(a, vmx):
    settings = {k: v for k, v in a.items() if k != "vm_id" and v is not None}
    return await get_client().update_vm(a["vm_id"], settings)


async def _h_vm_nic_create(a, vmx):
    return await get_client().create_nic(a["vm_id"], {"type": a["type"]})


async def _h_vm_folder_create(a, vmx):
    return await get_client().create_shared_folder(a["vm_id"], {"folder_id": a["folder_id"], "host_path": a["host_path"], "flags": a.get("flags", 0)})


async def _h_network_create(a, vmx):
    return await get_client().create_network({"name": a["name"], "type": a["type"]})


async def _h_network_portforward_set(a, vmx):
    return await get_client().update_portforward(a["vmnet"], a["protocol"], a["port"], {"guestIp": a["guest_ip"], "guestPort": a["guest_port"]})


# ---- 工作流组合工具：合并真实会话中的高频多连调用（copy_to→run→copy_from 占 647 次调用中的 68 圈）----

_JOB_RC_MARK = "__JOB_RC="
_POSIX_INTERPRETERS = {"bash": "/bin/bash", "sh": "/bin/sh"}
_CMD_INTERPRETERS = {"cmd", "cmd.exe"}


def _new_job_id() -> str:
    return secrets.token_hex(4)


def _job_host_temp(name: str) -> str:
    d = os.getenv("VMWARE_HOST_TEMP_DIR", "").strip() or tempfile.gettempdir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def _job_plan(interpreter: str, job_id: str, guest_dir: str) -> dict:
    """interpreter → 执行计划：guest 内脚本/输出路径、run 参数、脚本包装函数、宿主临时文件名。
    输出捕获原理：VIX 不回收程序 stdout，脚本被包装成把 stdout/stderr 重定向到 guest 内
    输出文件并在末尾追加退出码标记，服务端回拷后解析。"""
    interp = (interpreter or "bash").strip().lower()
    if interp.startswith("./"):
        interp = interp[2:]
    if interp in _POSIX_INTERPRETERS:
        base = (guest_dir or "/tmp").rstrip("/") or "/tmp"
        script, out = f"{base}/vmjob-{job_id}.sh", f"{base}/vmjob-{job_id}.out"

        def wrap(body):
            return ('{\n' + body.rstrip('\n') + f'\n}} > "{out}" 2>&1\n'
                    f'echo {_JOB_RC_MARK}$? >> "{out}"\n')

        return {"kind": "posix", "program": _POSIX_INTERPRETERS[interp], "run_args": [script],
                "script": script, "out": out, "wrap": wrap,
                "host_script": f"vmjob-{job_id}.sh", "host_out": f"vmjob-{job_id}.out",
                "enc": "utf-8", "newline": "\n"}
    if interp in _CMD_INTERPRETERS:
        base = (guest_dir or "C:/Windows/Temp").replace("/", "\\").rstrip("\\") or "C:\\Windows\\Temp"
        script, out = f"{base}\\vmjob-{job_id}.cmd", f"{base}\\vmjob-{job_id}.out"

        def wrap(body):
            return ('(\r\n' + body.rstrip('\r\n') + f'\r\n) > "{out}" 2>&1\r\n'
                    f'echo {_JOB_RC_MARK}%errorlevel%>>"{out}"\r\n')

        return {"kind": "cmd", "program": "cmd.exe", "run_args": ["/c", script],
                "script": script, "out": out, "wrap": wrap,
                "host_script": f"vmjob-{job_id}.cmd", "host_out": f"vmjob-{job_id}.out",
                "enc": "mbcs", "newline": "\r\n"}
    raise ToolError(
        f"unsupported interpreter: {interpreter}",
        tool="vmrun_run_job",
        hint="支持 bash/sh（POSIX）与 cmd/cmd.exe（Windows）；自带解释器路径的场景直接用 vmrun_run",
    )


async def _h_vmrun_run_job(a, vmx):
    vmrun = get_vmrun()
    path = await vmx(a["vm_id"])
    user, password = a.get("user", ""), a.get("password", "")
    job_id = _new_job_id()
    plan = _job_plan(a.get("interpreter", "bash"), job_id, a.get("guest_dir", ""))
    host_script = _job_host_temp(plan["host_script"])
    host_out = _job_host_temp(plan["host_out"])
    no_wait = bool(a.get("no_wait"))
    result = {
        "ok": True, "kind": plan["kind"], "job_id": job_id, "no_wait": no_wait,
        "script_guest_path": plan["script"], "output_guest_path": plan["out"],
        "exit_code": None,
    }
    try:
        with open(host_script, "w", encoding=plan["enc"], newline=plan["newline"]) as f:
            f.write(plan["wrap"](a["script"]))
        await vmrun.copy_to_guest(path, host_script, plan["script"], user=user, password=password)
        if no_wait:
            await vmrun.run_program(path, plan["program"], plan["run_args"], no_wait=True, user=user, password=password)
            result["note"] = ("no_wait：作业已启动，不等待不收集；用 vmrun_wait_file 等 output_guest_path 出现，"
                              "vmrun_read_file 取结果，收尾 vmrun_rm 清理 script/output 两个 guest 临时文件")
            return result
        try:
            await vmrun.run_program(path, plan["program"], plan["run_args"], user=user, password=password)
        except ToolError as e:
            raise ToolError(
                str(e), tool="vmrun_run_job",
                hint=(f"执行等待失败/超时，脚本可能仍在 guest 后台继续：稍后 vmrun_wait_file 等 {plan['out']}，"
                      f"再 vmrun_read_file 取结果（guest 临时文件已保留供取回）"),
            ) from e
        await vmrun.copy_from_guest(path, plan["out"], host_out, user=user, password=password)
        with open(host_out, "rb") as f:
            raw = f.read()
        text = decode_output(raw)
        result["stdout_bytes"] = len(raw)
        idx = text.rfind(_JOB_RC_MARK)
        if idx != -1:
            try:
                result["exit_code"] = int(text[idx + len(_JOB_RC_MARK):].splitlines()[0].strip())
            except (IndexError, ValueError):
                pass
            text = text[:idx].rstrip("\r\n")
        result["stdout"] = text
        if result["exit_code"] is None:
            result["hint"] = "输出中未找到退出码标记：作业可能未跑完（可用 vmrun_read_file 直读 output_guest_path 复核）"
        cleanup_failed = []
        for p in (plan["script"], plan["out"]):
            try:
                await vmrun.delete_file(path, p, user=user, password=password)
            except ToolError as e:
                cleanup_failed.append(f"{p}: {e}")
        if cleanup_failed:
            result["cleanup_failed"] = cleanup_failed
        return result
    finally:
        for p in (host_script, host_out):
            try:
                os.unlink(p)
            except OSError:
                pass


async def _h_vmrun_read_file(a, vmx):
    vmrun = get_vmrun()
    path = await vmx(a["vm_id"])
    cap = env_int("VMWARE_READ_FILE_KB", 256) * 1024
    host_tmp = _job_host_temp(f"vmread-{_new_job_id()}.bin")
    try:
        await vmrun.copy_from_guest(path, a["path"], host_tmp, user=a.get("user", ""), password=a.get("password", ""))
        with open(host_tmp, "rb") as f:
            raw = f.read(cap + 1)
        total = os.path.getsize(host_tmp)
    finally:
        try:
            os.unlink(host_tmp)
        except OSError:
            pass
    if b"\x00" in raw[:8192]:
        raise ToolError(
            f"binary file: {a['path']}", tool="vmrun_read_file",
            hint="内容含 NUL，疑似二进制文件；请用 vmrun_copy_from 拷回宿主处理",
        )
    return {"ok": True, "path": a["path"], "bytes": total,
            "truncated": total > cap, "content": decode_output(raw[:cap])}


async def _h_vmrun_wait_file(a, vmx):
    vmrun = get_vmrun()
    path = await vmx(a["vm_id"])
    user, password = a.get("user", ""), a.get("password", "")
    timeout_s = min(float(a.get("timeout_s") or 25), 600.0)
    interval_s = max(float(a.get("interval_s") or 2), 0.05)
    started = time.monotonic()
    polls, last = 0, ""
    while True:
        polls += 1
        try:
            await vmrun.file_exists(path, a["path"], user=user, password=password)
            return {"ok": True, "exists": True, "path": a["path"],
                    "waited_ms": int((time.monotonic() - started) * 1000), "polls": polls}
        except ToolError as e:
            last = str(e)
        if polls == 1:
            low = last.lower()
            if any(k in low for k in ("password", "tools", "not running", "vmx", "invalid")):
                raise ToolError(
                    last, tool="vmrun_wait_file",
                    hint="首次探测即失败且非「文件不存在」类错误（凭据/Tools/VMX 问题不会因等待好转）；请先 vm_health 自检",
                )
        elapsed = time.monotonic() - started
        if elapsed >= timeout_s:
            return {"ok": False, "exists": False, "path": a["path"],
                    "waited_ms": int(elapsed * 1000), "polls": polls,
                    "hint": f"等待超时文件仍未出现（最后一次错误：{last[:120]}）；作业可能未产出或已失败，可用 vmrun_ps 复核 guest 进程"}
        await asyncio.sleep(min(interval_s, max(0.05, timeout_s - elapsed)))


_ADAPTERS = {"c": get_client, "r": get_vmrun, "l": get_vmcli}


def _uniform(target, method, exts, fixed):
    async def h(a, vmx):
        args = [await e(a, vmx) for e in exts]
        r = await getattr(_ADAPTERS[target](), method)(*args)
        return fixed if fixed is not None else r
    return h


_ROUTES = {
    # ==================== REST（uniform） ====================
    "vm_get": ("c", "get_vm", (_R("vm_id"),), None),
    "vm_delete": ("c", "delete_vm", (_R("vm_id"),), _DELETED),
    "vm_power_get": ("c", "get_power_state", (_R("vm_id"),), None),
    "vm_power_set": ("c", "change_power_state", (_R("vm_id"), _R("state")), None),
    "vm_nic_list": ("c", "list_nics", (_R("vm_id"),), None),
    "vm_nic_delete": ("c", "delete_nic", (_R("vm_id"), _R("index")), _DELETED),
    "vm_ip_get": ("c", "get_vm_ip", (_R("vm_id"),), None),
    "vm_folder_list": ("c", "list_shared_folders", (_R("vm_id"),), None),
    "vm_folder_delete": ("c", "delete_shared_folder", (_R("vm_id"), _R("folder_id")), _DELETED),
    "network_list": ("c", "list_networks", (), None),
    "network_portforward_list": ("c", "get_portforwards", (_R("vmnet"),), None),
    "network_portforward_delete": ("c", "delete_portforward", (_R("vmnet"), _R("protocol"), _R("port")), _DELETED),
    # ==================== VMRUN（48，全部 uniform） ====================
    "vmrun_list": ("r", "list_running", (), None),
    "vmrun_clone": ("r", "clone", (_U, _R("dest_path"), _O("clone_type", "linked"), _O("snapshot", ""), _O("clone_name", "")), None),
    "vmrun_upgrade": ("r", "upgrade_vm", (_U,), None),
    "vmrun_delete": ("r", "delete_vm", (_U,), None),
    "vmrun_start": ("r", "start", (_U, _O("gui", True)), None),
    "vmrun_stop": ("r", "stop", (_U, _O("hard", False)), None),
    "vmrun_reset": ("r", "reset", (_U, _O("hard", False)), None),
    "vmrun_suspend": ("r", "suspend", (_U, _O("hard", False)), None),
    "vmrun_pause": ("r", "pause", (_U,), None),
    "vmrun_unpause": ("r", "unpause", (_U,), None),
    "vmrun_snapshot_list": ("r", "list_snapshots", (_U, _O("show_tree", False)), None),
    "vmrun_snapshot_take": ("r", "snapshot", (_U, _R("name")), None),
    "vmrun_snapshot_delete": ("r", "delete_snapshot", (_U, _R("name"), _O("delete_children", False)), None),
    "vmrun_snapshot_revert": ("r", "revert_to_snapshot", (_U, _R("name")), None),
    "vmrun_file_exists": ("r", "file_exists", (_U, _R("path"), *_UP), None),
    "vmrun_dir_exists": ("r", "directory_exists", (_U, _R("path"), *_UP), None),
    "vmrun_ls": ("r", "list_directory", (_U, _R("path"), *_UP), None),
    "vmrun_mkdir": ("r", "create_directory", (_U, _R("path"), *_UP), None),
    "vmrun_rmdir": ("r", "delete_directory", (_U, _R("path"), *_UP), None),
    "vmrun_rm": ("r", "delete_file", (_U, _R("path"), *_UP), None),
    "vmrun_rename": ("r", "rename_file", (_U, _R("old_path"), _R("new_path"), *_UP), None),
    "vmrun_copy_to": ("r", "copy_to_guest", (_U, _R("host_path"), _R("guest_path"), *_UP), None),
    "vmrun_copy_from": ("r", "copy_from_guest", (_U, _R("guest_path"), _R("host_path"), *_UP), None),
    "vmrun_temp_file": ("r", "create_temp_file", (_U, *_UP), None),
    "vmrun_copy_dir_to": ("r", "copy_dir_to", (_U, _R("host_path"), _R("guest_path"), _O("include", ""), _O("exclude", ""), *_UP), None),
    "vmrun_copy_dir_from": ("r", "copy_dir_from", (_U, _R("guest_path"), _R("host_path"), _O("include", ""), _O("exclude", ""), *_UP), None),
    "vmrun_run": ("r", "run_program", (_U, _R("program"), _O("args", ""), _O("no_wait", False), _C(False), _O("interactive", False), *_UP), None),
    "vmrun_script": ("r", "run_script", (_U, _R("interpreter"), _R("script"), _O("no_wait", False), _C(False), _C(False), *_UP), None),
    "vmrun_ps": ("r", "list_processes", (_U, *_UP), None),
    "vmrun_kill": ("r", "kill_process", (_U, _R("pid"), *_UP), None),
    "vmrun_shared_enable": ("r", "enable_shared_folders", (_U,), None),
    "vmrun_shared_disable": ("r", "disable_shared_folders", (_U,), None),
    "vmrun_shared_add": ("r", "add_shared_folder", (_U, _R("name"), _R("host_path")), None),
    "vmrun_shared_remove": ("r", "remove_shared_folder", (_U, _R("name")), None),
    "vmrun_shared_set": ("r", "set_shared_folder_state", (_U, _R("name"), _R("host_path"), _O("writable", True)), None),
    "vmrun_device_connect": ("r", "connect_device", (_U, _R("device")), None),
    "vmrun_device_disconnect": ("r", "disconnect_device", (_U, _R("device")), None),
    "vmrun_var_read": ("r", "read_variable", (_U, _R("var_type"), _R("name"), *_UP), None),
    "vmrun_var_write": ("r", "write_variable", (_U, _R("var_type"), _R("name"), _R("value"), *_UP), None),
    "vmrun_screenshot": ("r", "capture_screen", (_U, _R("output_path")), None),
    "vmrun_keystrokes": ("r", "type_keystrokes", (_U, _R("keystrokes")), None),
    "vmrun_tools_install": ("r", "install_tools", (_U,), None),
    "vmrun_tools_state": ("r", "check_tools_state", (_U,), None),
    "vmrun_guest_ip": ("r", "get_guest_ip", (_U, _O("wait", False)), None),
    "vmrun_host_networks": ("r", "list_host_networks", (), None),
    "vmrun_portforward_list": ("r", "list_port_forwardings", (_R("network"),), None),
    "vmrun_portforward_set": ("r", "set_port_forwarding", (_R("network"), _R("protocol"), _R("host_port"), _R("guest_ip"), _R("guest_port"), _O("description", "")), None),
    "vmrun_portforward_delete": ("r", "delete_port_forwarding", (_R("network"), _R("protocol"), _R("host_port")), None),
    # ==================== VMCLI（65，全部 uniform） ====================
    "snapshot_list": ("l", "snapshot_list", (_U,), None),
    "snapshot_take": ("l", "snapshot_take", (_U, _R("name")), None),
    "snapshot_revert": ("l", "snapshot_revert", (_U, _R("name")), None),
    "snapshot_delete": ("l", "snapshot_delete", (_U, _R("name"), _O("delete_children", False)), None),
    "snapshot_clone": ("l", "snapshot_clone", (_U, _R("snapshot_name"), _R("dest_path"), _O("clone_type", "linked")), None),
    "guest_run": ("l", "guest_run", (_U, _R("program"), _O("args", ""), *_UP), None),
    "guest_ps": ("l", "guest_ps", (_U, *_UP), None),
    "guest_kill": ("l", "guest_kill", (_U, _R("pid"), *_UP), None),
    "guest_ls": ("l", "guest_ls", (_U, _R("path"), *_UP), None),
    "guest_mkdir": ("l", "guest_mkdir", (_U, _R("path"), *_UP), None),
    "guest_rm": ("l", "guest_rm", (_U, _R("path"), *_UP), None),
    "guest_rmdir": ("l", "guest_rmdir", (_U, _R("path"), *_UP), None),
    "guest_copy_to": ("l", "guest_copy_to", (_U, _R("host_path"), _R("guest_path"), *_UP), None),
    "guest_copy_from": ("l", "guest_copy_from", (_U, _R("guest_path"), _R("host_path"), *_UP), None),
    "guest_env": ("l", "guest_env", (_U, *_UP), None),
    "mks_screenshot": ("l", "mks_screenshot", (_U, _R("output_path")), None),
    "mks_send_key": ("l", "mks_send_key", (_U, _R("key_sequence")), None),
    "mks_query": ("l", "mks_query", (_U,), None),
    "chipset_query": ("l", "chipset_query", (_U,), None),
    "chipset_set_cpu": ("l", "chipset_set_cpu", (_U, _R("count")), None),
    "chipset_set_memory": ("l", "chipset_set_memory", (_U, _R("size_mb")), None),
    "chipset_set_cores": ("l", "chipset_set_cores_per_socket", (_U, _R("cores")), None),
    "tools_query": ("l", "tools_query", (_U,), None),
    "tools_install": ("l", "tools_install", (_U,), None),
    "tools_upgrade": ("l", "tools_upgrade", (_U,), None),
    "template_create": ("l", "template_create", (_U, _R("template_path"), _R("name")), None),
    "template_deploy": ("l", "template_deploy", (_R("template_path"), _R("dest_path"), _R("name")), None),
    "disk_query": ("l", "disk_query", (_U,), None),
    "disk_create": ("l", "disk_create", (_U, _R("size_gb"), _O("disk_type", "scsi"), _O("adapter", 0), _O("device", 0)), None),
    "disk_extend": ("l", "disk_extend", (_U, _R("new_size_gb"), _O("adapter", 0), _O("device", 0)), None),
    "config_query": ("l", "config_query", (_U,), None),
    "config_set": ("l", "config_set", (_U, _R("key"), _R("value")), None),
    "power_query": ("l", "power_query", (_U,), None),
    "power_start": ("l", "power_start", (_U,), None),
    "power_stop": ("l", "power_stop", (_U,), None),
    "power_pause": ("l", "power_pause", (_U,), None),
    "power_unpause": ("l", "power_unpause", (_U,), None),
    "power_reset": ("l", "power_reset", (_U,), None),
    "power_suspend": ("l", "power_suspend", (_U,), None),
    "ethernet_query": ("l", "ethernet_query", (_U,), None),
    "ethernet_set_type": ("l", "ethernet_set_connection_type", (_U, _R("index"), _R("type")), None),
    "ethernet_set_present": ("l", "ethernet_set_present", (_U, _R("index"), _R("present")), None),
    "ethernet_set_connected": ("l", "ethernet_set_start_connected", (_U, _R("index"), _R("connected")), None),
    "ethernet_set_device": ("l", "ethernet_set_virtual_device", (_U, _R("index"), _R("device")), None),
    "ethernet_set_network": ("l", "ethernet_set_network_name", (_U, _R("index"), _R("name")), None),
    "ethernet_purge": ("l", "ethernet_purge", (_U, _R("index")), None),
    "hgfs_query": ("l", "hgfs_query", (_U,), None),
    "hgfs_set_enabled": ("l", "hgfs_set_enabled", (_U, _R("index"), _R("enabled")), None),
    "hgfs_set_path": ("l", "hgfs_set_host_path", (_U, _R("index"), _R("path")), None),
    "hgfs_set_name": ("l", "hgfs_set_guest_name", (_U, _R("index"), _R("name")), None),
    "hgfs_set_read": ("l", "hgfs_set_read_access", (_U, _R("index"), _R("read")), None),
    "hgfs_set_write": ("l", "hgfs_set_write_access", (_U, _R("index"), _R("write")), None),
    "serial_query": ("l", "serial_query", (_U,), None),
    "serial_set_present": ("l", "serial_set_present", (_U, _R("index"), _R("present")), None),
    "serial_purge": ("l", "serial_purge", (_U, _R("index")), None),
    "sata_query": ("l", "sata_query", (_U,), None),
    "sata_set_present": ("l", "sata_set_present", (_U, _R("adapter"), _R("present")), None),
    "sata_purge": ("l", "sata_purge", (_U, _R("adapter")), None),
    "nvme_query": ("l", "nvme_query", (_U,), None),
    "nvme_set_present": ("l", "nvme_set_present", (_U, _R("adapter"), _R("present")), None),
    "nvme_purge": ("l", "nvme_purge", (_U, _R("adapter")), None),
    "vprobes_query": ("l", "vprobes_query", (_U,), None),
    "vprobes_enable": ("l", "vprobes_set_enabled", (_U, _R("enabled")), None),
    "vprobes_load": ("l", "vprobes_load", (_U, _R("script_path")), None),
    "vprobes_reset": ("l", "vprobes_reset", (_U,), None),
}

_HANDLERS = {name: _uniform(*entry) for name, entry in _ROUTES.items()}
_HANDLERS.update({
    "set_vm_encryption_password": _h_set_vm_encryption_password,
    "vm_resolve": _h_vm_resolve,
    "vm_health": _h_vm_health,
    "vm_log_tail": _h_vm_log_tail,
    "screenshot_ocr": _h_screenshot_ocr,
    "vm_list": _h_vm_list,
    "vm_create": _h_vm_create,
    "vm_update": _h_vm_update,
    "vm_nic_create": _h_vm_nic_create,
    "vm_folder_create": _h_vm_folder_create,
    "network_create": _h_network_create,
    "network_portforward_set": _h_network_portforward_set,
    "vmrun_run_job": _h_vmrun_run_job,
    "vmrun_read_file": _h_vmrun_read_file,
    "vmrun_wait_file": _h_vmrun_wait_file,
})

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

    handler = _HANDLERS.get(name)
    result = _UNHANDLED if handler is None else await handler(a, vmx)

    if result is _UNHANDLED:
        raise ToolError(f"Unknown tool: {name}", tool=name, hint="工具名不存在；以 list_tools 返回为准")
    if isinstance(result, str):
        return [TextContent(type="text", text=_truncate_output(result) if result else "OK")]
    return [TextContent(type="text", text=_truncate_output(_dumps(result)) if result else "OK")]


def _dumps(result) -> str:
    """成功路径 JSON 序列化：默认 indent=2（兼容现状）；VMWARE_COMPACT_OUTPUT=1 时紧凑输出。
    失败路径（_error_content）不受影响，保持缩进便于人工排查。"""
    if os.getenv("VMWARE_COMPACT_OUTPUT", "").strip().lower() in ("1", "true", "yes", "on"):
        return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
    return json.dumps(result, indent=2, ensure_ascii=False)


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
