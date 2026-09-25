"""Structured tool error shared by the wrappers and the MCP server."""

# 错误文本子串 -> 一句话可行动建议（按顺序取第一个命中）
HINTS: tuple[tuple[str, str], ...] = (
    (
        "password is required",
        "VM 已加密：提供 enc_pass 参数，或设置 VMWARE_ENC_PASSWORD / 调用 set_vm_encryption_password 预存密码",
    ),
    (
        "password required",
        "VM 已加密：提供 enc_pass 参数，或设置 VMWARE_ENC_PASSWORD / 调用 set_vm_encryption_password 预存密码",
    ),
    ("incorrect password", "加密密码不正确：核对密码后重试"),
    (
        "does not support empty passwords",
        "guest 账户空密码被 VIX 硬拒绝：请为 guest 账户设置真实密码后重试",
    ),
    (
        "vmware tools",
        "guest 操作依赖 VMware Tools：先 vmrun_tools_state 检查，未安装则 vmrun_tools_install",
    ),
    ("invalid vmx file", "vmx 路径无效或文件损坏：用 vm_resolve / vm_list 确认路径"),
    ("was not found", "路径不存在：检查 vmx / 文件路径（注意区分宿主机与 guest 路径）"),
    ("cannot be found", "路径不存在：检查 vmx / 文件路径（注意区分宿主机与 guest 路径）"),
)


def make_hint(text: str) -> str:
    lowered = (text or "").lower()
    for pattern, hint in HINTS:
        if pattern in lowered:
            return hint
    return ""


class ToolError(Exception):
    """带结构化字段的工具失败；由 MCP server 序列化为 JSON 返回。"""

    def __init__(
        self,
        message: str,
        *,
        tool: str = "",
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        duration_ms: int | None = None,
        timeout: bool = False,
        hint: str = "",
    ):
        super().__init__(message)
        self.tool = tool
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms
        self.timeout = timeout
        self.hint = hint or make_hint(message)

    def to_dict(self) -> dict:
        return {
            "ok": False,
            "tool": self.tool,
            "error": str(self),
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timeout": self.timeout,
            "hint": self.hint,
        }
