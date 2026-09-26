"""VMware vmrun command line wrapper."""

import asyncio
import os
import time

from .errors import ToolError
from .runtime import decode_output, encryption_password, env_int, env_timeout, redact_secrets

# 超时分档（秒）：查询默认 30s，电源操作 90s，长任务 600s；可用 env 覆盖
_POWER_COMMANDS = {"start", "stop", "reset", "suspend", "pause", "unpause"}
_LONG_COMMANDS = {"clone", "upgradevm", "deleteVM", "installTools"}

# 子进程并发上限：多客户端/子代理并发调用时防止 vmrun 进程风暴拖垮宿主
_SUBPROCESS_SLOTS = asyncio.Semaphore(env_int("VMWARE_MAX_CONCURRENCY", 8))


def _timeout_for(command: str) -> float:
    if command in _POWER_COMMANDS:
        return env_timeout("VMWARE_TIMEOUT_POWER", 90.0)
    if command in _LONG_COMMANDS:
        return env_timeout("VMWARE_TIMEOUT_LONG", 600.0)
    return env_timeout("VMWARE_TIMEOUT_QUERY", 30.0)


class VMRun:
    """Wrapper for vmrun command line tool."""

    def __init__(self, vmrun_path: str | None = None):
        self.vmrun_path = vmrun_path or os.getenv(
            "VMRUN_PATH",
            r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
        )

    async def _run(self, command: str, *args: str, guest_user: str = "", guest_pass: str = "", timeout: float | None = None) -> str:
        cmd = [self.vmrun_path, "-T", "ws"]
        enc = encryption_password()
        if enc:
            cmd.extend(["-vp", enc])
        if guest_user:
            cmd.extend(["-gu", guest_user])
        if guest_pass:
            cmd.extend(["-gp", guest_pass])
        cmd.append(command)
        cmd.extend(args)

        limit = timeout or _timeout_for(command)
        started = time.monotonic()
        # stdin 接 DEVNULL：vmrun 等密码/等输入时立即报错退出，
        # 而不是继承 MCP 服务端的 stdio 管道挂死（历史上 p90=30s 超时墙的根因）
        async with _SUBPROCESS_SLOTS:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=limit)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise ToolError(
                    f"vmrun {command} timed out after {limit:g}s, process killed",
                    timeout=True,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    hint="clone/upgrade 等长任务可用 VMWARE_TIMEOUT_LONG 提高上限；"
                         "否则检查 VM 是否卡在等加密密码（enc_pass）或等 VMware Tools",
                )
        duration_ms = int((time.monotonic() - started) * 1000)
        secrets = tuple(s for s in (encryption_password(), guest_pass) if s)
        out = redact_secrets(decode_output(stdout), secrets)
        err = redact_secrets(decode_output(stderr), secrets)

        if proc.returncode != 0:
            error_msg = err.strip() or out.strip()
            raise ToolError(
                f"vmrun failed: {redact_secrets(error_msg, secrets)}",
                exit_code=proc.returncode,
                stdout=out.strip(),
                stderr=err.strip(),
                duration_ms=duration_ms,
            )

        return out.strip()

    # === Power ===
    async def start(self, vmx_path: str, gui: bool = True) -> str:
        return await self._run("start", vmx_path, "gui" if gui else "nogui")

    async def stop(self, vmx_path: str, hard: bool = False) -> str:
        return await self._run("stop", vmx_path, "hard" if hard else "soft")

    async def reset(self, vmx_path: str, hard: bool = False) -> str:
        return await self._run("reset", vmx_path, "hard" if hard else "soft")

    async def suspend(self, vmx_path: str, hard: bool = False) -> str:
        return await self._run("suspend", vmx_path, "hard" if hard else "soft")

    async def pause(self, vmx_path: str) -> str:
        return await self._run("pause", vmx_path)

    async def unpause(self, vmx_path: str) -> str:
        return await self._run("unpause", vmx_path)

    # === General ===
    async def list_running(self) -> str:
        return await self._run("list")

    async def upgrade_vm(self, vmx_path: str) -> str:
        return await self._run("upgradevm", vmx_path)

    async def delete_vm(self, vmx_path: str) -> str:
        return await self._run("deleteVM", vmx_path)

    async def clone(self, vmx_path: str, dest_path: str, clone_type: str = "linked", snapshot: str = "", clone_name: str = "") -> str:
        args = [vmx_path, dest_path, clone_type]
        if snapshot:
            args.append(f"-snapshot={snapshot}")
        if clone_name:
            args.append(f"-cloneName={clone_name}")
        return await self._run("clone", *args)

    # === Snapshot ===
    async def list_snapshots(self, vmx_path: str, show_tree: bool = False) -> str:
        args = [vmx_path]
        if show_tree:
            args.append("showTree")
        return await self._run("listSnapshots", *args)

    async def snapshot(self, vmx_path: str, name: str) -> str:
        return await self._run("snapshot", vmx_path, name)

    async def delete_snapshot(self, vmx_path: str, name: str, delete_children: bool = False) -> str:
        args = [vmx_path, name]
        if delete_children:
            args.append("andDeleteChildren")
        return await self._run("deleteSnapshot", *args)

    async def revert_to_snapshot(self, vmx_path: str, name: str) -> str:
        return await self._run("revertToSnapshot", vmx_path, name)

    # === Guest File Operations ===
    async def file_exists(self, vmx_path: str, guest_path: str, user: str = "", password: str = "") -> str:
        return await self._run("fileExistsInGuest", vmx_path, guest_path, guest_user=user, guest_pass=password)

    async def directory_exists(self, vmx_path: str, guest_path: str, user: str = "", password: str = "") -> str:
        return await self._run("directoryExistsInGuest", vmx_path, guest_path, guest_user=user, guest_pass=password)

    async def rename_file(self, vmx_path: str, old_path: str, new_path: str, user: str = "", password: str = "") -> str:
        return await self._run("renameFileInGuest", vmx_path, old_path, new_path, guest_user=user, guest_pass=password)

    async def create_temp_file(self, vmx_path: str, user: str = "", password: str = "") -> str:
        return await self._run("CreateTempfileInGuest", vmx_path, guest_user=user, guest_pass=password)

    async def list_directory(self, vmx_path: str, guest_path: str, user: str = "", password: str = "") -> str:
        return await self._run("listDirectoryInGuest", vmx_path, guest_path, guest_user=user, guest_pass=password)

    async def create_directory(self, vmx_path: str, guest_path: str, user: str = "", password: str = "") -> str:
        return await self._run("createDirectoryInGuest", vmx_path, guest_path, guest_user=user, guest_pass=password)

    async def delete_directory(self, vmx_path: str, guest_path: str, user: str = "", password: str = "") -> str:
        return await self._run("deleteDirectoryInGuest", vmx_path, guest_path, guest_user=user, guest_pass=password)

    async def delete_file(self, vmx_path: str, guest_path: str, user: str = "", password: str = "") -> str:
        return await self._run("deleteFileInGuest", vmx_path, guest_path, guest_user=user, guest_pass=password)

    async def copy_to_guest(self, vmx_path: str, host_path: str, guest_path: str, user: str = "", password: str = "") -> str:
        return await self._run("CopyFileFromHostToGuest", vmx_path, host_path, guest_path, guest_user=user, guest_pass=password)

    async def copy_from_guest(self, vmx_path: str, guest_path: str, host_path: str, user: str = "", password: str = "") -> str:
        return await self._run("CopyFileFromGuestToHost", vmx_path, guest_path, host_path, guest_user=user, guest_pass=password)

    # === Guest Process Operations ===
    async def run_program(self, vmx_path: str, program: str, args: str | list[str] = "", no_wait: bool = False, active_window: bool = False, interactive: bool = False, user: str = "", password: str = "") -> str:
        cmd_args = [vmx_path]
        if no_wait:
            cmd_args.append("-noWait")
        if active_window:
            cmd_args.append("-activeWindow")
        if interactive:
            cmd_args.append("-interactive")
        cmd_args.append(program)
        if args:
            # 字符串整体作为单个 argv 元素透传（不再 split，含空格路径不被拆碎）；多参数请传列表
            if isinstance(args, str):
                cmd_args.append(args)
            else:
                cmd_args.extend(args)
        return await self._run("runProgramInGuest", *cmd_args, guest_user=user, guest_pass=password)

    async def run_script(self, vmx_path: str, interpreter: str, script: str, no_wait: bool = False, active_window: bool = False, interactive: bool = False, user: str = "", password: str = "") -> str:
        cmd_args = [vmx_path]
        if no_wait:
            cmd_args.append("-noWait")
        if active_window:
            cmd_args.append("-activeWindow")
        if interactive:
            cmd_args.append("-interactive")
        cmd_args.extend([interpreter, script])
        return await self._run("runScriptInGuest", *cmd_args, guest_user=user, guest_pass=password)

    async def list_processes(self, vmx_path: str, user: str = "", password: str = "") -> str:
        return await self._run("listProcessesInGuest", vmx_path, guest_user=user, guest_pass=password)

    async def kill_process(self, vmx_path: str, pid: int, user: str = "", password: str = "") -> str:
        return await self._run("killProcessInGuest", vmx_path, str(pid), guest_user=user, guest_pass=password)

    # === Shared Folders ===
    async def enable_shared_folders(self, vmx_path: str) -> str:
        return await self._run("enableSharedFolders", vmx_path)

    async def disable_shared_folders(self, vmx_path: str) -> str:
        return await self._run("disableSharedFolders", vmx_path)

    async def add_shared_folder(self, vmx_path: str, name: str, host_path: str) -> str:
        return await self._run("addSharedFolder", vmx_path, name, host_path)

    async def remove_shared_folder(self, vmx_path: str, name: str) -> str:
        return await self._run("removeSharedFolder", vmx_path, name)

    async def set_shared_folder_state(self, vmx_path: str, name: str, host_path: str, writable: bool = True) -> str:
        return await self._run("setSharedFolderState", vmx_path, name, host_path, "writable" if writable else "readonly")

    # === Device ===
    async def connect_device(self, vmx_path: str, device_name: str) -> str:
        return await self._run("connectNamedDevice", vmx_path, device_name)

    async def disconnect_device(self, vmx_path: str, device_name: str) -> str:
        return await self._run("disconnectNamedDevice", vmx_path, device_name)

    # === Variables ===
    async def read_variable(self, vmx_path: str, var_type: str, name: str, user: str = "", password: str = "") -> str:
        return await self._run("readVariable", vmx_path, var_type, name, guest_user=user, guest_pass=password)

    async def write_variable(self, vmx_path: str, var_type: str, name: str, value: str, user: str = "", password: str = "") -> str:
        return await self._run("writeVariable", vmx_path, var_type, name, value, guest_user=user, guest_pass=password)

    # === Screen/Input ===
    async def capture_screen(self, vmx_path: str, output_path: str) -> str:
        return await self._run("captureScreen", vmx_path, output_path)

    async def type_keystrokes(self, vmx_path: str, keystrokes: str) -> str:
        return await self._run("typeKeystrokesInGuest", vmx_path, keystrokes)

    # === Tools ===
    async def install_tools(self, vmx_path: str) -> str:
        return await self._run("installTools", vmx_path)

    async def check_tools_state(self, vmx_path: str) -> str:
        return await self._run("checkToolsState", vmx_path)

    # === Network ===
    async def get_guest_ip(self, vmx_path: str, wait: bool = False) -> str:
        args = [vmx_path]
        if wait:
            args.append("-wait")
        return await self._run("getGuestIPAddress", *args)

    async def list_host_networks(self) -> str:
        return await self._run("listHostNetworks")

    async def list_port_forwardings(self, network: str) -> str:
        return await self._run("listPortForwardings", network)

    async def set_port_forwarding(self, network: str, protocol: str, host_port: int, guest_ip: str, guest_port: int, description: str = "") -> str:
        args = [network, protocol, str(host_port), guest_ip, str(guest_port)]
        if description:
            args.append(description)
        return await self._run("setPortForwarding", *args)

    async def delete_port_forwarding(self, network: str, protocol: str, host_port: int) -> str:
        return await self._run("deletePortForwarding", network, protocol, str(host_port))

    # === Directory Transfer ===
    async def copy_dir_to(self, vmx_path: str, host_dir: str, guest_dir: str, include: str = "", exclude: str = "", user: str = "", password: str = "") -> dict:
        """递归复制宿主机目录树到 guest。include/exclude 为逗号分隔的后缀过滤（如 ".txt,.log"）。"""
        host_dir = os.path.abspath(host_dir)
        if not os.path.isdir(host_dir):
            raise ToolError(f"host directory not found: {host_dir}", hint="检查宿主机目录路径")
        inc = tuple(s.strip().lower() for s in include.split(",") if s.strip())
        exc = tuple(s.strip().lower() for s in exclude.split(",") if s.strip())
        guest_base = guest_dir.rstrip("\\/").replace("/", "\\")
        created: set[str] = set()
        copied: list[str] = []
        failed: list[dict] = []
        for root, _dirs, files in os.walk(host_dir):
            for fname in files:
                lowered = fname.lower()
                if inc and not lowered.endswith(inc):
                    continue
                if exc and lowered.endswith(exc):
                    continue
                src = os.path.join(root, fname)
                rel = os.path.relpath(src, host_dir).replace(os.sep, "\\")
                dst = guest_base + "\\" + rel
                parent_parts = rel.split("\\")[:-1]
                ok = True
                for i in range(len(parent_parts)):
                    sub = guest_base + "\\" + "\\".join(parent_parts[: i + 1])
                    if sub not in created:
                        try:
                            await self.create_directory(vmx_path, sub, user, password)
                        except ToolError as e:
                            # 目录已存在视为成功；其余错误记录并跳过该文件
                            if "exist" not in str(e).lower():
                                failed.append({"file": rel, "error": str(e)})
                                ok = False
                                break
                        created.add(sub)
                if not ok:
                    continue
                try:
                    await self.copy_to_guest(vmx_path, src, dst, user, password)
                    copied.append(dst)
                except ToolError as e:
                    failed.append({"file": rel, "error": str(e)})
        return {"source": host_dir, "destination": guest_base, "copied_count": len(copied), "copied": copied, "failed": failed}

    async def copy_dir_from(self, vmx_path: str, guest_dir: str, host_dir: str, include: str = "", exclude: str = "", user: str = "", password: str = "") -> dict:
        """递归复制 guest 目录树到宿主机（依赖 vmrun ls 输出解析，无法解析的行计入 skipped）。"""
        host_dir = os.path.abspath(host_dir)
        os.makedirs(host_dir, exist_ok=True)
        inc = tuple(s.strip().lower() for s in include.split(",") if s.strip())
        exc = tuple(s.strip().lower() for s in exclude.split(",") if s.strip())
        guest_base = guest_dir.rstrip("\\/").replace("/", "\\")
        copied: list[str] = []
        failed: list[dict] = []
        skipped: list[str] = []
        guest_files = await self._walk_guest(vmx_path, guest_base, user, password, failed, skipped)
        for gfile in guest_files:
            lowered = gfile.lower()
            if inc and not lowered.endswith(inc):
                continue
            if exc and lowered.endswith(exc):
                continue
            rel = gfile[len(guest_base):].lstrip("\\")
            dst = os.path.join(host_dir, *rel.split("\\"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                await self.copy_from_guest(vmx_path, gfile, dst, user, password)
                copied.append(dst)
            except ToolError as e:
                failed.append({"file": gfile, "error": str(e)})
        return {"source": guest_base, "destination": host_dir, "copied_count": len(copied), "copied": copied, "failed": failed, "skipped": skipped}

    async def _walk_guest(self, vmx_path: str, guest_dir: str, user: str, password: str, failed: list, skipped: list) -> list[str]:
        files: list[str] = []
        stack = [guest_dir]
        while stack:
            current = stack.pop()
            try:
                listing = await self.list_directory(vmx_path, current, user, password)
            except ToolError as e:
                failed.append({"dir": current, "error": str(e)})
                continue
            for line in listing.splitlines():
                line = line.strip()
                if not line:
                    continue
                parsed = self._parse_ls_entry(line)
                if parsed is None:
                    skipped.append(line)
                    continue
                entry_name, is_dir = parsed
                full = current + "\\" + entry_name
                if is_dir:
                    stack.append(full)
                else:
                    files.append(full)
        return files

    @staticmethod
    def _parse_ls_entry(line: str) -> tuple[str, bool] | None:
        """解析 vmrun listDirectoryInGuest 单行，返回 (名称, 是否目录)。

        vmrun 输出布局未在全部 guest OS 上验证，这里做容错解析：
        含 <dir> 标记按目录处理（名称取标记之后的文本）；文件行以时间 token
        （含 ':'）之后的剩余部分为名称。无法解析返回 None，由调用方记录。
        """
        if "<dir>" in line:
            name = line.split("<dir>", 1)[1].strip()
            return (name, True) if name else None
        tokens = line.split()
        if len(tokens) >= 4:
            # 时间 token（含 ':'）可能在第 2~5 位（日期是 '07-13-2016' 或 'Nov 06 2024' 布局）
            time_idx = next((i for i, t in enumerate(tokens[:6]) if ":" in t), None)
            if time_idx is not None and time_idx + 1 < len(tokens):
                return " ".join(tokens[time_idx + 1:]), False
        return None
