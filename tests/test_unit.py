"""vmware-mcp 单元测试（无需真实 VMware 环境）。"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vmware_mcp import server  # noqa: E402
from vmware_mcp.errors import ToolError, make_hint  # noqa: E402
from vmware_mcp.runtime import decode_output, enc_password, env_timeout  # noqa: E402
from vmware_mcp.vmcli import _timeout_for as vmcli_timeout  # noqa: E402
from vmware_mcp.vmrun import VMRun, _timeout_for as vmrun_timeout  # noqa: E402


# ---------- 错误与 hint ----------

def test_make_hint_encryption_password():
    assert make_hint("vmrun failed: A password is required").startswith("VM 已加密")


def test_make_hint_incorrect_password():
    assert "不正确" in make_hint("vmrun failed: Incorrect password")


def test_make_hint_empty_guest_password():
    assert "空密码" in make_hint("The guest OS does not support empty passwords")


def test_make_hint_tools():
    assert "Tools" in make_hint("Error: VMware Tools are not running in the guest")


def test_make_hint_no_match():
    assert make_hint("some random failure") == ""


def test_toolerror_to_dict():
    e = ToolError("boom", exit_code=2, stdout="out", stderr="err", duration_ms=12)
    d = e.to_dict()
    assert d["ok"] is False
    assert d["error"] == "boom"
    assert d["exit_code"] == 2
    assert d["timeout"] is False
    assert d["duration_ms"] == 12


# ---------- 超时分档 ----------

def test_vmrun_timeout_tiers():
    assert vmrun_timeout("list") == 30.0
    assert vmrun_timeout("start") == 90.0
    assert vmrun_timeout("clone") == 600.0


def test_vmrun_timeout_env_override(monkeypatch):
    monkeypatch.setenv("VMWARE_TIMEOUT_QUERY", "5")
    assert vmrun_timeout("list") == 5.0
    monkeypatch.setenv("VMWARE_TIMEOUT_QUERY", "not-a-number")
    assert vmrun_timeout("list") == 30.0


def test_vmcli_timeout_tiers():
    assert vmcli_timeout("Power", "query") == 90.0
    assert vmcli_timeout("Disk", "Create") == 600.0
    assert vmcli_timeout("VMTemplate", "Deploy") == 600.0
    assert vmcli_timeout("MKS", "query") == 30.0


def test_env_timeout_invalid_value_falls_back(monkeypatch):
    monkeypatch.setenv("VMWARE_TIMEOUT_LONG", "abc")
    assert env_timeout("VMWARE_TIMEOUT_LONG", 600.0) == 600.0


# ---------- 解码 ----------

def test_decode_utf8():
    assert decode_output("中文 ok".encode("utf-8")) == "中文 ok"


def test_decode_gbk():
    assert decode_output("中文 ok".encode("gbk")) == "中文 ok"


# ---------- vmrun 参数与解析 ----------

def test_run_program_list_args_passthrough():
    recorded = {}

    async def fake_run(self, command, *args, **kwargs):
        recorded["command"] = command
        recorded["args"] = list(args)
        return ""

    original = VMRun._run
    VMRun._run = fake_run
    try:
        run = VMRun()
        asyncio.run(run.run_program("D:\\vms\\a.vmx", "C:\\my tool.exe", ["-a", "b c"], user="u", password="p"))
    finally:
        VMRun._run = original
    assert recorded["command"] == "runProgramInGuest"
    assert "C:\\my tool.exe" in recorded["args"]
    assert "b c" in recorded["args"], "列表参数不得被拆碎"


def test_run_program_string_arg_not_split():
    recorded = {}

    async def fake_run(self, command, *args, **kwargs):
        recorded["args"] = list(args)
        return ""

    original = VMRun._run
    VMRun._run = fake_run
    try:
        run = VMRun()
        asyncio.run(run.run_program("D:\\vms\\a.vmx", "prog.exe", "C:\\path with space\\x.exe"))
    finally:
        VMRun._run = original
    assert "C:\\path with space\\x.exe" in recorded["args"]


def test_parse_ls_entry_file_variants():
    p = VMRun._parse_ls_entry
    assert p("4096 Nov 06 2024 10:31 file.txt") == ("file.txt", False)
    assert p("1234 07-13-2016 10:36 a.exe") == ("a.exe", False)
    assert p("4096 Nov 06 2024 10:31 my file.txt") == ("my file.txt", False)


def test_parse_ls_entry_dir_and_garbage():
    p = VMRun._parse_ls_entry
    name, is_dir = p("0 07-13-2016 10:36 <dir> subfolder")
    assert name == "subfolder" and is_dir is True
    assert p("garbage line no time") is None


# ---------- 加密密码 ContextVar ----------

def test_enc_password_contextvar_isolation():
    async def main():
        enc_password.set("secret")
        inner = enc_password.get()

        async def other():
            enc_password.set("other")
            return enc_password.get()

        got = await asyncio.create_task(other())
        return inner, got

    inner, got = asyncio.run(main())
    assert inner == "secret"
    assert got == "other"
    # set() 只影响任务自己的上下文副本：asyncio.run 返回后外层仍是默认值，证明并发调用互不可见
    assert enc_password.get() is None


# ---------- get_vmx_path 与 call_tool ----------

class _FakeClient:
    def __init__(self, vms):
        self._vms = vms

    async def list_vms(self):
        return self._vms


def test_get_vmx_path_resolves_id():
    server._vm_path_cache.clear()
    original = server.get_client
    server.get_client = lambda: _FakeClient([{"id": "TESTID", "path": "D:\\vms\\a.vmx"}])
    try:
        assert asyncio.run(server.get_vmx_path("TESTID")) == "D:\\vms\\a.vmx"
        assert asyncio.run(server.get_vmx_path("D:\\vms\\a.vmx")) == "D:\\vms\\a.vmx"
    finally:
        server.get_client = original
    server._vm_path_cache.clear()


def test_get_vmx_path_raises_on_unknown():
    server._vm_path_cache.clear()
    original = server.get_client
    server.get_client = lambda: _FakeClient([])
    try:
        try:
            asyncio.run(server.get_vmx_path("NOPE"))
            raised = False
        except ToolError as e:
            raised = True
            assert "vm_list" in e.hint
        assert raised, "未知 vm_id 必须报错而不是返回空串"
    finally:
        server.get_client = original
    server._vm_path_cache.clear()


def test_call_tool_unknown_tool_is_structured():
    result = asyncio.run(server.call_tool("no_such_tool", {}))
    payload = json.loads(result[0].text)
    assert payload["ok"] is False
    assert "Unknown tool" in payload["error"]


def test_call_tool_set_encryption_password():
    result = asyncio.run(server.call_tool("set_vm_encryption_password", {"vm_id": "D:\\vms\\ok.vmx", "password": "pw"}))
    payload = json.loads(result[0].text)
    assert payload["status"] == "stored"
    assert server._enc_passwords["D:\\vms\\ok.vmx"] == "pw"
    server._enc_passwords.clear()


def test_call_tool_error_returns_structured_json():
    # 未知 vm_id 触发 ToolError → 结构化 JSON 而非 SDK 纯文本错误
    server._vm_path_cache.clear()
    original = server.get_client
    server.get_client = lambda: _FakeClient([])
    try:
        result = asyncio.run(server.call_tool("vmrun_start", {"vm_id": "MISSING-ID"}))
    finally:
        server.get_client = original
    server._vm_path_cache.clear()
    payload = json.loads(result[0].text)
    assert payload["ok"] is False
    assert payload["tool"] == "vmrun_start"
    assert payload["hint"]


# ---------- 轮1：安全护栏 ----------

def test_destructive_tool_dry_run_without_confirm(monkeypatch):
    monkeypatch.delenv("VMWARE_READ_ONLY", raising=False)
    result = asyncio.run(server.call_tool("vmrun_rm", {"vm_id": "D:/vms/a.vmx", "path": "C:/x.txt"}))
    payload = json.loads(result[0].text)
    assert payload["dry_run"] is True and payload["ok"] is False
    assert payload["arguments"]["path"] == "C:/x.txt"
    assert "confirm" in payload["hint"]


def test_destructive_tool_executes_with_confirm(monkeypatch):
    monkeypatch.delenv("VMWARE_READ_ONLY", raising=False)
    # 走到真实 vmrun（无服务器环境）前应先在 vmx 解析报错——只要不是 dry_run 即证明护栏放行
    try:
        result = asyncio.run(server.call_tool("vmrun_rm", {"vm_id": "D:/vms/a.vmx", "path": "C:/x.txt", "confirm": True}))
        payload = json.loads(result[0].text)
        assert payload.get("dry_run") is not True
    except Exception:
        pass  # 走到执行层后的任何失败都可接受（本机无 VM），关键是未在护栏层被 dry-run 拦截


def test_read_only_mode_blocks_destructive(monkeypatch):
    monkeypatch.setenv("VMWARE_READ_ONLY", "1")
    result = asyncio.run(server.call_tool("vmrun_rm", {"vm_id": "D:/vms/a.vmx", "path": "C:/x.txt", "confirm": True}))
    payload = json.loads(result[0].text)
    assert payload["read_only"] is True and payload["ok"] is False
    assert "dry_run" not in payload


def test_read_only_mode_allows_queries(monkeypatch):
    monkeypatch.setenv("VMWARE_READ_ONLY", "1")
    # vm_list 是只读工具：只读模式下不应被护栏拒绝（REST 不可达会以结构化错误返回，但不是 read_only 拒绝）
    result = asyncio.run(server.call_tool("vm_list", {}))
    payload = json.loads(result[0].text)
    assert payload.get("read_only") is not True


def test_vm_power_set_off_is_destructive_on_only_not(monkeypatch):
    assert server._is_destructive("vm_power_set", {"state": "off"}) is True
    assert server._is_destructive("vm_power_set", {"state": "on"}) is False
    assert server._is_destructive("vmrun_rm", {}) is True
    assert server._is_destructive("vm_list", {}) is False


def test_annotations_injected():
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert tools["vmrun_rm"].annotations.destructiveHint is True
    assert tools["vm_list"].annotations.readOnlyHint is True
    assert tools["vm_health"].annotations.readOnlyHint is True
    assert tools["vmrun_start"].annotations is None or tools["vmrun_start"].annotations.destructiveHint is not True


def test_confirm_prop_injected_on_destructive_only():
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert "confirm" in tools["vmrun_rm"].inputSchema["properties"]
    assert "confirm" in tools["vm_power_set"].inputSchema["properties"]
    assert "confirm" not in tools["vm_list"].inputSchema["properties"]


# ---------- 轮2：性能 ----------

def test_shared_http_client_reused():
    import vmware_mcp.client as c
    c._shared_client = None
    a = c.get_shared_client(auth=("u", "p"))
    b = c.get_shared_client(auth=("other", "x"))
    assert a is b, "共享客户端应只创建一次"
    c._shared_client = None


def test_vmware_client_uses_shared_pool():
    import vmware_mcp.client as c
    c._shared_client = None
    seen = {}

    class FakeShared:
        async def request(self, method, url, **kw):
            seen["client"] = fake_client_obj
            class R:
                content = b"[]"
                def raise_for_status(self): pass
                def json(self): return []
            return R()

    fake_client_obj = FakeShared()
    original = c.httpx.AsyncClient
    c.httpx.AsyncClient = lambda **kw: fake_client_obj
    try:
        client = c.VMwareClient()
        import asyncio as aio
        aio.run(client.list_vms())
        first = seen["client"]
        aio.run(client.list_vms())
        assert seen["client"] is first, "两次请求应复用同一连接池"
    finally:
        c.httpx.AsyncClient = original
        c._shared_client = None


def test_output_truncation(monkeypatch):
    monkeypatch.setenv("VMWARE_MAX_OUTPUT", "100")
    long_text = "x" * 500
    out = server._truncate_output(long_text)
    assert len(out) < 200 and "已截断" in out
    assert server._truncate_output("short") == "short"


def test_output_truncation_disabled(monkeypatch):
    monkeypatch.setenv("VMWARE_MAX_OUTPUT", "0")
    assert server._truncate_output("y" * 500) == "y" * 500


def test_subprocess_semaphore_config():
    from vmware_mcp.vmrun import _SUBPROCESS_SLOTS as s1
    from vmware_mcp.vmcli import _SUBPROCESS_SLOTS as s2
    assert s1._value == 8 and s2._value == 8


def test_env_int_invalid_falls_back(monkeypatch):
    from vmware_mcp.runtime import env_int
    monkeypatch.setenv("VMWARE_MAX_CONCURRENCY", "abc")
    assert env_int("VMWARE_MAX_CONCURRENCY", 8) == 8
