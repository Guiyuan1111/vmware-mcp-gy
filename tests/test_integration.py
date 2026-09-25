"""集成测试：需要真实 VMware Workstation + vmrest。

默认跳过；设 VMWARE_IT=1 启用（pytest -m integration）。验证项对应
《vmware-mcp调用体验与开发建议.md》第六节验收清单中可在本机自动化执行的部分。
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv("VMWARE_IT"), reason="需要 VMWARE_IT=1 与真实 VMware 环境"),
]


def _call(name, args):
    from vmware_mcp import server

    result = asyncio.run(server.call_tool(name, args))
    return json.loads(result[0].text)


def test_vm_list_ok():
    payload = _call("vm_list", {})
    assert isinstance(payload, list)


def test_vm_health_on_first_vm():
    vms = _call("vm_list", {})
    if not vms:
        pytest.skip("无已注册 VM")
    vm_id = vms[0].get("path") or vms[0].get("id")
    payload = _call("vm_health", {"vm_id": vm_id})
    assert "encryptionType" in payload
    assert "running" in payload


def test_unknown_vm_fast_fail_under_5s():
    import time

    start = time.monotonic()
    payload = _call("vmrun_tools_state", {"vm_id": "DEFINITELY-MISSING-ID"})
    elapsed = time.monotonic() - start
    assert payload["ok"] is False
    assert elapsed < 5, f"未知 VM 应快速失败，实际 {elapsed:.1f}s"
    # vmrun list 依赖网络扫描时可能略慢，但不应挂到客户端 30s 超时
    assert elapsed < 25
