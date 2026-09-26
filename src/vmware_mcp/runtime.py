"""Shared per-call runtime state and helpers for the vmrun/vmcli wrappers."""

import contextvars
import os

# 加密密码按"每次工具调用"隔离（ContextVar 由 server.call_tool 在解析 vmx 路径时写入，
# 并发调用互不可见）。None = 本次调用与加密无关（如宿主机级命令），"" = 是 VM 操作但无密码。
enc_password: contextvars.ContextVar["str | None"] = contextvars.ContextVar(
    "vmware_enc_password", default=None
)


def encryption_password() -> str:
    """当前调用的加密密码（由 server 端解析：显式参数 > 预存密码 > env）。"""
    return enc_password.get() or ""


def decode_output(data: bytes) -> str:
    """解码子进程输出：utf-8 优先，失败退 gb18030（GBK 超集，覆盖中文 Windows 的控制台输出）。"""
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def env_timeout(env_name: str, default: float) -> float:
    raw = os.getenv(env_name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(env_name: str, default: int) -> int:
    raw = os.getenv(env_name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def redact_secrets(text: str, secrets: tuple[str, ...]) -> str:
    """把文本中出现的密钥值替换为 ***。

    -gp/-vp 密码经 argv 明文传递是 vmrun/vmcli 的机制限制（无法根治）；
    本函数保证密钥至少不出现在 MCP 错误输出与日志中。
    """
    if not text:
        return text
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text
