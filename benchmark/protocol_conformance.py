# -*- coding: utf-8 -*-
"""protocol_conformance —— stdio 协议符合性门（无需 VMware）

对真实启动命令建立 JSON-RPC stdio 会话，断言 mcp-server-builder skill 红线项：
  1. stdout 纯净：会话全程每行 stdout 都是合法 JSON-RPC（协议通道零污染）
  2. initialize 握手 + tools/list 全量 140 工具
  3. 未知工具调用 → 结构化错误且会话不崩
  4. 破坏性工具无 confirm → dry-run 预览（护栏经协议层在线）
  5. VMWARE_TOOLS=rest 作用域：工具面裁剪为 rest+core，域外调用被守卫拒绝

用法：python benchmark/protocol_conformance.py
"""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_CMD = [sys.executable, "-c",
              "import sys; sys.path.insert(0, r'%s'); from vmware_mcp.server import main; main()" % (ROOT / "src")]
TIMEOUT = 30


class StdioClient:
    """最小 JSON-RPC stdio 客户端：记录会话全程 stdout 行用于纯净度断言。"""

    def __init__(self, env=None):
        self.proc = subprocess.Popen(
            SERVER_CMD, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
        self.stdout_lines = []
        self._t = threading.Thread(target=self._pump, daemon=True)
        self._t.start()
        self._id = 0

    def _pump(self):
        for line in self.proc.stdout:
            self.stdout_lines.append(line.rstrip("\n"))

    def notify(self, method, params=None):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None):
        self._id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        return self._wait_result(self._id)

    def _wait_result(self, want_id):
        import time
        deadline = time.time() + TIMEOUT
        seen = 0
        while time.time() < deadline:
            for line in self.stdout_lines[seen:]:
                seen += 1
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 纯净度断言在收尾统一做
                if msg.get("id") == want_id and ("result" in msg or "error" in msg):
                    return msg
            import time as _t
            _t.sleep(0.05)
        raise TimeoutError(f"no response for id={want_id}")

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.kill()


CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))


def run_session(env=None, scoped=False):
    c = StdioClient(env=env)
    try:
        init = c.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                        "clientInfo": {"name": "conformance", "version": "0"}})
        c.notify("notifications/initialized")
        tools = c.request("tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        check("initialize 握手", init["result"]["serverInfo"]["name"] == "vmware-mcp")
        if scoped:
            check("VMWARE_TOOLS=rest 工具面裁剪", len(tools) == 22 and "vmrun_copy_from" not in names and "vm_health" in names,
                  f"got {len(tools)} tools")
            guard = c.request("tools/call", {"name": "vmrun_wait_file", "arguments": {"vm_id": "D:/vms/a.vmx", "path": "/x"}})
            payload = json.loads(guard["result"]["content"][0]["text"])
            check("域外工具被守卫拒绝", payload.get("ok") is False and "VMWARE_TOOLS=rest" in payload.get("error", ""))
        else:
            check("tools/list 全量 140", len(tools) == 140, f"got {len(tools)}")
            unknown = c.request("tools/call", {"name": "__no_such_tool__", "arguments": {}})
            payload = json.loads(unknown["result"]["content"][0]["text"])
            check("未知工具结构化错误", payload.get("ok") is False and "Unknown tool" in payload.get("error", ""))
            dry = c.request("tools/call", {"name": "vmrun_delete", "arguments": {"vm_id": "D:/vms/a.vmx", "path": "C:/x"}})
            text = dry["result"]["content"][0]["text"]
            payload = json.loads(text)
            check("破坏性工具 dry-run 护栏", payload.get("dry_run") is True and payload.get("ok") is False
                  and "confirm" in payload.get("hint", ""))
        return c
    except Exception:
        c.close()
        raise


def main():
    c = run_session()
    core_ok = c.proc.poll() is None  # 护栏错误后服务器必须仍然存活
    check("错误路径后服务器存活", core_ok)
    c.close()

    c2 = run_session(env={**os.environ, "VMWARE_TOOLS": "rest"}, scoped=True)
    c2.close()

    # stdout 纯净度：会话全程每行都必须是合法 JSON（协议通道零污染）
    bad = []
    for lines in (c.stdout_lines, c2.stdout_lines):
        for line in lines:
            if line.strip():
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    bad.append(line[:80])
    check("stdout 全程 JSON-RPC 零污染", not bad, f"污染行: {bad[:3]}")

    failed = [n for n, ok, _ in CHECKS if not ok]
    print(f"\n{'CONFORMANCE PASS' if not failed else 'CONFORMANCE FAIL: ' + ', '.join(failed)}"
          f"  ({len(CHECKS) - len(failed)}/{len(CHECKS)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
