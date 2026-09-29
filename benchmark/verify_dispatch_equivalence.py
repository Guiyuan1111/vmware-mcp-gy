# -*- coding: utf-8 -*-
"""verify_dispatch_equivalence —— call_tool 分发行为黄金快照校验

对全部 140 个工具（+1 个未知工具名）以 schema 生成的参数驱动 call_tool，
client/vmrun/vmcli 全部替换为记录桩（记录方法名与实参），产出
「方法调用序列 + 返回文本」快照。路由表重构前后各跑一次，逐字节 diff，
证明重构零行为变化（安全性/稳定性/兼容性红线）。

用法：
  python benchmark/verify_dispatch_equivalence.py record   # 录制快照（仅重构前用）
  python benchmark/verify_dispatch_equivalence.py check    # 与快照比对（回归门）
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vmware_mcp import server  # noqa: E402

# 组合工具含随机 job_id 与宿主临时目录 → 快照要求确定性，固定之
os.environ["VMWARE_HOST_TEMP_DIR"] = str(Path(__file__).resolve().parent / "_bench_tmp")
server._new_job_id = lambda: "deadbeef"

GOLDEN = Path(__file__).resolve().parent / "goldens_dispatch.json"

# 记录桩：任何方法调用 → 记录 (方法名, 实参)，返回确定性 JSON 值
FIRST_ENUM = True


def _fake_return(method, args=()):
    if method == "list_vms":
        return []  # 唯一被迭代的集合返回值
    if method == "list_running":
        return "Total running VMs: 0"  # vm_health 对返回值调 .lower()，须为字符串
    if method == "copy_from_guest":
        # vmrun_run_job / vmrun_read_file 成功路径需要宿主侧真的出现回拷文件
        try:
            with open(args[2], "wb") as f:
                f.write(b"hello from guest\n__JOB_RC=0\n")
        except OSError:
            pass
        return "Copy: file transferred"
    return {"fake": method}


class _Recorder:
    def __init__(self, tag):
        self.tag = tag
        self.calls = []

    def __getattr__(self, method):
        async def m(*args, **kw):
            self.calls.append([method, _jsonable(args), _jsonable(kw)])
            return _fake_return(method, args)
        return m


def _jsonable(x):
    return json.loads(json.dumps(x, ensure_ascii=False, default=str))


def gen_args(schema):
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    out = {}
    for key, spec in props.items():
        if key == "confirm":
            out[key] = True  # 穿过 dry-run 护栏，考察真实分发
            continue
        t = spec.get("type", "string")
        if "enum" in spec:
            out[key] = spec["enum"][0]
        elif t == "integer":
            out[key] = 1
        elif t == "number":
            out[key] = 1.5
        elif t == "boolean":
            out[key] = False
        elif key == "vm_id":
            out[key] = "D:/vms/bench.vmx"  # .vmx 直通路径，无 REST 依赖
        else:
            out[key] = "bench-str"
    # 必填缺失容错：required 里的键若未生成（不该发生），补字符串
    for k in required:
        out.setdefault(k, "bench-str")
    return out


async def capture():
    tools = await server.list_tools()
    names = [t.name for t in tools] + ["__NO_SUCH_TOOL__"]
    report = {}
    for name in names:
        tool = next((t for t in tools if t.name == name), None)
        args = gen_args(tool.inputSchema) if tool else {}

        fc, fr, fl = _Recorder("c"), _Recorder("r"), _Recorder("l")
        server._client, server._vmrun, server._vmcli = fc, fr, fl
        server._enc_passwords.clear()
        server._vm_path_cache.clear()
        try:
            content = await asyncio.wait_for(server.call_tool(name, dict(args)), timeout=20)
            result = [c.text for c in content]
        except Exception as e:  # 等价性考察对象：异常种类与消息也要一致
            result = [f"EXC:{type(e).__name__}:{e}"]
        calls = {"c": fc.calls, "r": fr.calls, "l": fl.calls}
        report[name] = {"args": args, "calls": calls, "result": result}
    return report


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    report = asyncio.run(capture())
    if mode == "record":
        GOLDEN.write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        print(f"recorded {len(report)} entries -> {GOLDEN.name}")
        return
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    diffs = []
    for name in sorted(set(golden) | set(report)):
        g, r = golden.get(name), report.get(name)
        if g != r:
            for field in ("args", "calls", "result"):
                if (g or {}).get(field) != (r or {}).get(field):
                    diffs.append(f"{name}.{field}:\n  gold={json.dumps((g or {}).get(field), ensure_ascii=False)[:300]}\n  new ={json.dumps((r or {}).get(field), ensure_ascii=False)[:300]}")
    if diffs:
        print(f"FAIL: {len(diffs)} field diffs")
        print("\n".join(diffs[:20]))
        sys.exit(1)
    print(f"EQUIVALENT: {len(report)}/{len(golden)} entries byte-identical")


if __name__ == "__main__":
    main()
