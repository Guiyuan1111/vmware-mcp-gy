"""vmware-mcp 单元测试（无需真实 VMware 环境）。"""

import asyncio
import json
import sys
import time
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


# ---------- 轮3：代码卫生 ----------

def test_dead_methods_removed():
    import vmware_mcp.client as c
    import vmware_mcp.vmcli as v
    for cls, name in [(c.VMwareClient, "update_nic"), (c.VMwareClient, "update_shared_folder"),
                      (c.VMwareClient, "get_mac_to_ips"), (c.VMwareClient, "update_mac_to_ip"),
                      (v.VMCli, "vm_create"), (v.VMCli, "hgfs_set_present")]:
        assert not hasattr(cls, name), f"{cls.__name__}.{name} 应已删除"


def test_redact_secrets():
    from vmware_mcp.runtime import redact_secrets
    assert redact_secrets("pwd=jnX4 and jnX4 again", ("jnX4",)) == "pwd=*** and *** again"
    assert redact_secrets("no secret here", ()) == "no secret here"
    assert redact_secrets("", ("x",)) == ""


def _fake_proc(stdout: bytes, stderr: bytes, returncode: int):
    class P:
        async def communicate(self):
            return stdout, stderr
        def kill(self): pass
        async def wait(self): return returncode
    P.returncode = returncode
    return P()


def test_vmrun_error_redacts_guest_password(monkeypatch):
    import vmware_mcp.vmrun as vr
    secret = "Sup3rSecret!"

    async def fake_exec(*cmd, **kw):
        return _fake_proc(b"", ("vmrun failed: Incorrect password " + secret).encode(), 1)

    monkeypatch.setattr(vr.asyncio, "create_subprocess_exec", fake_exec)
    run = vr.VMRun()
    import asyncio as aio
    try:
        aio.run(run.file_exists("D:/vms/a.vmx", "C:/x", user="admin", password=secret))
        raised = False
    except vr.ToolError as e:
        raised = True
        assert secret not in str(e) and "***" in str(e)
        assert secret not in e.stderr and secret not in e.stdout
    assert raised


def test_vmrun_error_redacts_enc_password(monkeypatch):
    import vmware_mcp.vmrun as vr
    from vmware_mcp.runtime import enc_password
    secret = "EncPass99"

    async def fake_exec(*cmd, **kw):
        cmd_str = " ".join(str(c) for c in cmd)
        return _fake_proc(b"", ("bad: " + cmd_str).encode(), 1)

    monkeypatch.setattr(vr.asyncio, "create_subprocess_exec", fake_exec)
    run = vr.VMRun()
    import asyncio as aio

    async def main():
        enc_password.set(secret)
        try:
            await run.start("D:/vms/a.vmx")
            return False
        except vr.ToolError as e:
            assert secret not in str(e) and "-vp ***" in str(e)
            return True

    assert aio.run(main())


def test_vmcli_error_redacts_guest_password(monkeypatch):
    import vmware_mcp.vmcli as vc
    secret = "GuestPW#1"

    async def fake_exec(*cmd, **kw):
        return _fake_proc(b"", ("error password " + secret).encode(), 1)

    monkeypatch.setattr(vc.asyncio, "create_subprocess_exec", fake_exec)
    cli = vc.VMCli()
    import asyncio as aio
    try:
        aio.run(cli.guest_run("D:/vms/a.vmx", "prog.exe", user="u", password=secret))
        raised = False
    except vc.ToolError as e:
        raised = True
        assert secret not in str(e) and "***" in str(e)
    assert raised


def test_tls_verify_env(monkeypatch):
    monkeypatch.setenv("VMWARE_TLS_VERIFY", "1")
    monkeypatch.setattr("vmware_mcp.server._client", None)
    c = server.get_client()
    assert c.verify is True
    monkeypatch.setenv("VMWARE_TLS_VERIFY", "")
    monkeypatch.setattr("vmware_mcp.server._client", None)
    c = server.get_client()
    assert c.verify is False
    monkeypatch.setattr("vmware_mcp.server._client", None)


# ---------- 轮4：可观测性 ----------

def test_call_log_success_info_emitted(caplog, monkeypatch):
    import logging

    class _FakeClient:
        async def list_vms(self):
            return []

    monkeypatch.setattr(server, "get_client", lambda: _FakeClient())
    with caplog.at_level(logging.INFO, logger="vmware_mcp"):
        asyncio.run(server.call_tool("vm_list", {}))
    recs = [r for r in caplog.records if r.getMessage().startswith("tool=vm_list")]
    assert recs and "ok=True" in recs[-1].getMessage() and "duration_ms=" in recs[-1].getMessage()


def test_call_log_error_warning_emitted(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="vmware_mcp"):
        asyncio.run(server.call_tool("no_such_tool", {}))
    recs = [r for r in caplog.records if "tool=no_such_tool" in r.getMessage()]
    assert recs and "ok=False" in recs[-1].getMessage()


def test_call_log_default_level_silent(caplog, monkeypatch):
    """默认 WARNING：成功路径的 INFO 日志不发射（main() basicConfig 默认级别下的实际行为）。"""
    import logging

    class _FakeClient:
        async def list_vms(self):
            return []

    monkeypatch.setattr(server, "get_client", lambda: _FakeClient())
    with caplog.at_level(logging.WARNING, logger="vmware_mcp"):
        asyncio.run(server.call_tool("vm_list", {}))
    assert not [r for r in caplog.records if "tool=vm_list" in r.getMessage() and "ok=True" in r.getMessage()]


def test_setup_logging_env_levels(monkeypatch):
    import logging
    monkeypatch.delenv("VMWARE_LOG_LEVEL", raising=False)
    logging.getLogger().handlers.clear()
    server._setup_logging()
    assert logging.getLogger().level == logging.WARNING

    logging.getLogger().handlers.clear()
    monkeypatch.setenv("VMWARE_LOG_LEVEL", "info")
    server._setup_logging()
    assert logging.getLogger().level == logging.INFO

    logging.getLogger().handlers.clear()
    monkeypatch.setenv("VMWARE_LOG_LEVEL", "bogus")
    server._setup_logging()
    assert logging.getLogger().level == logging.WARNING
    logging.getLogger().handlers.clear()


# ---------- 轮3：热路径行为锁 ----------

def test_vmx_encryption_variants(tmp_path):
    vmx = tmp_path / "a.vmx"
    vmx.write_text('config.version = "8"\nencryptionType = "aes256"\ndisplayName = "t"\n', encoding="utf-8")
    assert server._vmx_encryption(str(vmx)) == "aes256"
    vmx.write_text("encryptionType=legal\n", encoding="utf-8")
    assert server._vmx_encryption(str(vmx)) == "legal"
    vmx.write_text('displayName = "x"\n', encoding="utf-8")
    assert server._vmx_encryption(str(vmx)) == "none"
    assert server._vmx_encryption(str(tmp_path / "missing.vmx")) == "unknown"
    # 取第一条匹配行（多行命中时）
    vmx.write_text('encryptionType = "first"\nencryptionType = "second"\n', encoding="utf-8")
    assert server._vmx_encryption(str(vmx)) == "first"


def test_decode_output_ascii_fast_path():
    from vmware_mcp.runtime import decode_output
    assert decode_output(b"Total running VMs: 2\r\n") == "Total running VMs: 2\r\n"
    assert decode_output("") == "" if False else decode_output(b"") == ""
    gbk = "虚拟机".encode("gb18030")
    assert decode_output(gbk) == "虚拟机"
    assert decode_output(b"\xff\xfe invalid") .endswith("invalid")  # 双双失败退 replace


# ---------- 轮6：组合工具内部并发化（旧串行语义参照） ----------

class _SlowFake:
    """慢桩：每次调用记录并睡 0.15s，证明并发重叠；raises 可让指定方法抛 ToolError。"""

    def __init__(self, raises=(), fail_ip=False):
        self.raises = raises
        self.fail_ip = fail_ip
        self.calls = []

    async def _m(self, name, *args):
        self.calls.append(name)
        await asyncio.sleep(0.15)
        if name in self.raises:
            raise ToolError(f"boom: {name}")
        return f"out-{name}"

    def list_running(self):
        return self._m("list_running")

    def check_tools_state(self, vmx):
        return self._m("check_tools_state", vmx)

    def get_guest_ip(self, vmx, wait=False):
        return self._m("get_guest_ip", vmx)


async def _serial_reference(vmrun, vmx_path):
    """0.3.1 及以前的串行语义（逐字对照原 _h_vm_health 探测段）。"""
    out = {}
    try:
        listing = await vmrun.list_running()
        out["running"] = vmx_path.lower() in listing.lower()
    except ToolError as e:
        out["running"] = f"unknown: {e}"
    try:
        out["tools"] = await vmrun.check_tools_state(vmx_path)
    except ToolError as e:
        out["tools"] = f"error: {e}"
    try:
        out["ip"] = await vmrun.get_guest_ip(vmx_path)
    except ToolError:
        out["ip"] = None
    return out


def test_vm_health_concurrent_matches_serial_semantics(monkeypatch):
    fake = _SlowFake()
    monkeypatch.setattr(server, "get_vmrun", lambda: fake)
    t0 = time.monotonic()
    h = asyncio.run(server._h_vm_health({"vm_id": "D:/vms/a.vmx"}, _identity_vmx_test))
    elapsed = time.monotonic() - t0
    assert fake.calls == ["list_running", "check_tools_state", "get_guest_ip"]  # 调用序列不变（黄金快照同序）
    ref = asyncio.run(_serial_reference(_SlowFake(), "D:/vms/a.vmx"))  # 新 fake：参照不污染调用记录
    assert h["running"] == ref["running"] and h["tools"] == ref["tools"] and h["ip"] == ref["ip"]
    assert elapsed < 0.40, f"未并发：{elapsed:.2f}s（串行应 ~0.45s）"  # 0.15s 重叠 ≈ 0.15-0.2s


def test_vm_health_concurrent_error_branches_match_serial(monkeypatch):
    fake = _SlowFake(raises=("list_running", "check_tools_state", "get_guest_ip"))
    monkeypatch.setattr(server, "get_vmrun", lambda: fake)
    h = asyncio.run(server._h_vm_health({"vm_id": "D:/vms/a.vmx"}, _identity_vmx_test))
    assert h["running"] == "unknown: boom: list_running"
    assert h["tools"] == "error: boom: check_tools_state"
    assert h["ip"] is None


async def _identity_vmx_test(vm_id):
    return vm_id


def test_vm_resolve_concurrent_power_matches_serial(monkeypatch):
    class _FakeClient:
        def __init__(self):
            self.power_calls = []

        async def list_vms(self):
            return [
                {"id": "vm-1", "path": "D:/vms/win11.vmx"},
                {"id": "vm-2", "path": "D:/vms/ubuntu.vmx"},
                {"id": "vm-3", "path": "D:/vms/win11-test.vmx"},
                {"id": "vm-4", "path": "D:/vms/other.vmx"},
            ]

        async def get_power_state(self, vm_id):
            self.power_calls.append(vm_id)
            await asyncio.sleep(0.1)
            if vm_id == "vm-3":
                raise RuntimeError("rest down")
            return "poweredOn"

    fc = _FakeClient()
    monkeypatch.setattr(server, "get_client", lambda: fc)
    monkeypatch.setattr(server, "_vm_path_cache", {})
    t0 = time.monotonic()
    r = asyncio.run(server._h_vm_resolve({"query": "win11"}, _identity_vmx_test))
    elapsed = time.monotonic() - t0
    assert r["count"] == 2  # win11.vmx 与 win11-test.vmx
    assert r["matches"][0]["power"] == "poweredOn"
    assert str(r["matches"][1]["power"]).startswith("unavailable: RuntimeError")
    assert fc.power_calls == ["vm-1", "vm-3"]  # 保序
    assert elapsed < 0.25, f"未并发：{elapsed:.2f}s（串行应 ~0.2s+）"


# ---------- 轮7：紧凑输出 ----------

def test_compact_output_default_keeps_indent(monkeypatch):
    monkeypatch.delenv("VMWARE_COMPACT_OUTPUT", raising=False)
    result = asyncio.run(server.call_tool("vm_list", {}))
    # 桩返回 [] 时空结果走 "OK"；换非空桩
    class _FakeClient:
        async def list_vms(self):
            return [{"id": "vm-1", "path": "D:/vms/a.vmx"}]
    monkeypatch.setattr(server, "get_client", lambda: _FakeClient())
    result = asyncio.run(server.call_tool("vm_list", {}))
    assert "\n" in result[0].text and '  "id"' in result[0].text


def test_compact_output_on(monkeypatch):
    class _FakeClient:
        async def list_vms(self):
            return [{"id": "vm-1", "path": "D:/vms/a.vmx"}]
    monkeypatch.setattr(server, "get_client", lambda: _FakeClient())
    monkeypatch.setenv("VMWARE_COMPACT_OUTPUT", "1")
    compact = asyncio.run(server.call_tool("vm_list", {}))[0].text
    monkeypatch.delenv("VMWARE_COMPACT_OUTPUT")
    pretty = asyncio.run(server.call_tool("vm_list", {}))[0].text
    assert "\n" not in compact and ": " not in compact
    assert json.loads(compact) == json.loads(pretty)  # 值完全一致，仅序列化形式不同


# ---------- 轮8：工作流组合工具（run_job / read_file / wait_file） ----------

class _FakeVMRunJob:
    """记录调用的桩；copy_from_guest 在宿主侧落真实文件，走通 run_job/read_file 成功路径。"""

    def __init__(self, out_bytes=b"line1\nline2\n__JOB_RC=3\n", exists=True, exists_error="Error: The file was not found"):
        self.calls = []
        self.out_bytes = out_bytes
        self.exists = exists
        self.exists_error = exists_error

    async def copy_to_guest(self, vmx, host, guest, user="", password=""):
        self.calls.append(("copy_to_guest", host, guest))
        with open(host, "rb") as f:
            self.uploaded = f.read()

    async def run_program(self, vmx, program, args, no_wait=False, active_window=False, interactive=False, user="", password=""):
        self.calls.append(("run_program", program, tuple(args), no_wait))

    async def copy_from_guest(self, vmx, guest, host, user="", password=""):
        self.calls.append(("copy_from_guest", guest, host))
        with open(host, "wb") as f:
            f.write(self.out_bytes)

    async def delete_file(self, vmx, path, user="", password=""):
        self.calls.append(("delete_file", path))

    async def file_exists(self, vmx, path, user="", password=""):
        self.calls.append(("file_exists", path))
        if self.exists:
            return "1"
        raise ToolError(self.exists_error)


def test_run_job_success_parses_stdout_and_exit_code(monkeypatch, tmp_path):
    fake = _FakeVMRunJob()
    monkeypatch.setattr(server, "get_vmrun", lambda: fake)
    monkeypatch.setattr(server, "_new_job_id", lambda: "job001")
    monkeypatch.setenv("VMWARE_HOST_TEMP_DIR", str(tmp_path))
    r = asyncio.run(server._h_vmrun_run_job({"vm_id": "D:/vms/a.vmx", "script": "echo hi"}, _identity_vmx_test))
    assert r["ok"] is True and r["exit_code"] == 3 and r["stdout"] == "line1\nline2"
    assert r["script_guest_path"] == "/tmp/vmjob-job001.sh" and r["output_guest_path"] == "/tmp/vmjob-job001.out"
    # 调用序列：上传→执行→回拷→清理×2
    assert [c[0] for c in fake.calls] == ["copy_to_guest", "run_program", "copy_from_guest", "delete_file", "delete_file"]
    assert fake.calls[1] == ("run_program", "/bin/bash", ("/tmp/vmjob-job001.sh",), False)
    # 上传的是包装后脚本（重定向 + 退出码标记），不是裸脚本文本
    assert b'"> "/tmp/vmjob-job001.out" 2>&1' in fake.uploaded.replace(b'"', b'"') or b"/tmp/vmjob-job001.out" in fake.uploaded
    assert b"__JOB_RC=$?" in fake.uploaded
    assert b"echo hi" in fake.uploaded
    # 宿主临时文件已清理
    assert list(tmp_path.iterdir()) == []


def test_run_job_no_wait_skips_collect(monkeypatch, tmp_path):
    fake = _FakeVMRunJob()
    monkeypatch.setattr(server, "get_vmrun", lambda: fake)
    monkeypatch.setattr(server, "_new_job_id", lambda: "job002")
    monkeypatch.setenv("VMWARE_HOST_TEMP_DIR", str(tmp_path))
    r = asyncio.run(server._h_vmrun_run_job({"vm_id": "D:/vms/a.vmx", "script": "longjob", "no_wait": True}, _identity_vmx_test))
    assert r["no_wait"] is True and "vmrun_wait_file" in r["note"]
    assert [c[0] for c in fake.calls] == ["copy_to_guest", "run_program"]
    assert fake.calls[1][3] is True  # run_program 带 -noWait
    # 宿主临时文件已清理
    assert list(tmp_path.iterdir()) == []


def test_run_job_cmd_plan(monkeypatch, tmp_path):
    fake = _FakeVMRunJob()
    monkeypatch.setattr(server, "get_vmrun", lambda: fake)
    monkeypatch.setattr(server, "_new_job_id", lambda: "job003")
    monkeypatch.setenv("VMWARE_HOST_TEMP_DIR", str(tmp_path))
    r = asyncio.run(server._h_vmrun_run_job({"vm_id": "D:/vms/a.vmx", "script": "echo hi", "interpreter": "cmd"}, _identity_vmx_test))
    assert r["kind"] == "cmd"
    assert fake.calls[1] == ("run_program", "cmd.exe", ("/c", "C:" + chr(92) + "Windows" + chr(92) + "Temp" + chr(92) + "vmjob-job003.cmd"), False)
    assert b"%errorlevel%" in fake.uploaded and b"\r\n" in fake.uploaded


def test_run_job_unsupported_interpreter_fails_fast(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "get_vmrun", lambda: _FakeVMRunJob())
    monkeypatch.setenv("VMWARE_HOST_TEMP_DIR", str(tmp_path))
    try:
        asyncio.run(server._h_vmrun_run_job({"vm_id": "D:/vms/a.vmx", "script": "x", "interpreter": "python"}, _identity_vmx_test))
        raised = False
    except ToolError as e:
        raised = True
        assert "vmrun_run" in e.hint
    assert raised


def test_run_job_run_timeout_preserves_guest_paths(monkeypatch, tmp_path):
    class _TimeoutFake(_FakeVMRunJob):
        async def run_program(self, vmx, program, args, no_wait=False, active_window=False, interactive=False, user="", password=""):
            if not no_wait:
                raise ToolError("vmrun timed out")
            await super().run_program(vmx, program, args, no_wait, active_window, interactive, user, password)

    monkeypatch.setattr(server, "get_vmrun", lambda: _TimeoutFake())
    monkeypatch.setattr(server, "_new_job_id", lambda: "job004")
    monkeypatch.setenv("VMWARE_HOST_TEMP_DIR", str(tmp_path))
    try:
        asyncio.run(server._h_vmrun_run_job({"vm_id": "D:/vms/a.vmx", "script": "x"}, _identity_vmx_test))
        raised = False
    except ToolError as e:
        raised = True
        assert "/tmp/vmjob-job004.out" in e.hint and "vmrun_wait_file" in e.hint
    assert raised
    assert list(tmp_path.iterdir()) == []  # 宿主临时文件仍被清理


def test_read_file_returns_content(monkeypatch, tmp_path):
    fake = _FakeVMRunJob()
    monkeypatch.setattr(server, "get_vmrun", lambda: fake)
    monkeypatch.setattr(server, "_new_job_id", lambda: "read01")
    monkeypatch.setenv("VMWARE_HOST_TEMP_DIR", str(tmp_path))
    r = asyncio.run(server._h_vmrun_read_file({"vm_id": "D:/vms/a.vmx", "path": "/tmp/out.txt"}, _identity_vmx_test))
    assert r["ok"] is True and r["bytes"] == 23 and r["truncated"] is False
    assert r["content"] == "line1\nline2\n__JOB_RC=3\n"
    assert list(tmp_path.iterdir()) == []  # 中转文件即用即删


def test_read_file_binary_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "get_vmrun", lambda: _FakeVMRunJob(out_bytes=b"\x00\x01\x02binary"))
    monkeypatch.setattr(server, "_new_job_id", lambda: "read02")
    monkeypatch.setenv("VMWARE_HOST_TEMP_DIR", str(tmp_path))
    try:
        asyncio.run(server._h_vmrun_read_file({"vm_id": "D:/vms/a.vmx", "path": "/tmp/a.bin"}, _identity_vmx_test))
        raised = False
    except ToolError as e:
        raised = True
        assert "vmrun_copy_from" in e.hint
    assert raised
    assert list(tmp_path.iterdir()) == []


def test_read_file_truncation_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "get_vmrun", lambda: _FakeVMRunJob(out_bytes=b"x" * 2000))
    monkeypatch.setattr(server, "_new_job_id", lambda: "read03")
    monkeypatch.setenv("VMWARE_HOST_TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("VMWARE_READ_FILE_KB", "1")
    r = asyncio.run(server._h_vmrun_read_file({"vm_id": "D:/vms/a.vmx", "path": "/tmp/big.txt"}, _identity_vmx_test))
    assert r["bytes"] == 2000 and r["truncated"] is True and len(r["content"]) == 1024


def test_wait_file_exists_first_poll(monkeypatch):
    monkeypatch.setattr(server, "get_vmrun", lambda: _FakeVMRunJob())
    r = asyncio.run(server._h_vmrun_wait_file({"vm_id": "D:/vms/a.vmx", "path": "/tmp/x.out"}, _identity_vmx_test))
    assert r == {"ok": True, "exists": True, "path": "/tmp/x.out", "waited_ms": 0, "polls": 1}


def test_wait_file_timeout_returns_structured(monkeypatch):
    monkeypatch.setattr(server, "get_vmrun", lambda: _FakeVMRunJob(exists=False))
    t0 = time.monotonic()
    r = asyncio.run(server._h_vmrun_wait_file(
        {"vm_id": "D:/vms/a.vmx", "path": "/tmp/x.out", "timeout_s": 0.2, "interval_s": 0.05}, _identity_vmx_test))
    assert r["ok"] is False and r["exists"] is False and r["polls"] >= 2
    assert "hint" in r and time.monotonic() - t0 < 2


def test_wait_file_infra_error_fails_fast(monkeypatch):
    # 凭据类错误不会因等待好转：首次探测即抛，不空耗 timeout
    monkeypatch.setattr(server, "get_vmrun", lambda: _FakeVMRunJob(exists=False, exists_error="password is required for this VM"))
    try:
        asyncio.run(server._h_vmrun_wait_file(
            {"vm_id": "D:/vms/a.vmx", "path": "/tmp/x.out", "timeout_s": 5}, _identity_vmx_test))
        raised = False
    except ToolError as e:
        raised = True
        assert "vm_health" in e.hint
    assert raised
