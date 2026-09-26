# -*- coding: utf-8 -*-
"""bench_composite —— 组合工具内部并发化基准（0.3.2 轮6，真实 VMware Workstation，全只读）

vm_health 内部 3 个探测（list_running / check_tools_state / get_guest_ip）互不依赖：
  old = 0.3.1 串行执行（逐个 await）
  new = 0.3.2 asyncio.gather 并发执行（并发上限仍受 vmrun._run 全局信号量约束）
等价性：结果逐字段一致 + 调用序列不变（tests/test_unit.py 专项锁定；黄金快照门全绿）。

vm_resolve 的多匹配电源查询并发同理，量级取决于匹配数（每次省 ~300ms/匹配）。
需要本机 vmrun.exe 与至少一台运行中的 VM；否则打印 SKIP。
运行：python benchmark/bench_composite.py [N]
"""
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH.parent / "src"))

import shutil  # noqa: E402
import os  # noqa: E402

VMRUN = os.getenv("VMRUN_PATH", "") or shutil.which("vmrun") or ""
if not VMRUN:
    for cand in (r"C:/Program Files/VMware/VMware Workstation/vmrun.exe",
                 r"C:/Program Files (x86)/VMware/VMware Workstation/vmrun.exe"):
        if Path(cand).exists():
            VMRUN = cand
            break
if not VMRUN:
    print("SKIP: 未找到 vmrun.exe（设 VMRUN_PATH 后重试）")
    sys.exit(0)
os.environ["VMRUN_PATH"] = VMRUN

from vmware_mcp import server  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3


async def _identity_vmx(vm_id):
    return vm_id


async def main():
    vmrun = server.get_vmrun()
    await vmrun.list_running()  # 预热
    listing = await vmrun.list_running()
    vmx_lines = [l for l in listing.splitlines() if l.lower().endswith(".vmx")]
    if not vmx_lines:
        print("SKIP: 无运行中 VM（vm_health 的 guest 探测需要一台运行中的 VM）")
        sys.exit(0)
    vmx_path = vmx_lines[0]

    # old：串行（= 0.3.1 行为，逐个 await 同一组只读探测）
    xs_old = []
    for _ in range(N):
        t0 = time.perf_counter()
        await server._health_running(vmrun, vmx_path)
        await server._health_tools(vmrun, vmx_path)
        await server._health_ip(vmrun, vmx_path)
        xs_old.append((time.perf_counter() - t0) * 1000)

    # new：完整 vm_health（含并发探测 + 本地文件读取，均为真实路径）
    xs_new = []
    for _ in range(N):
        t0 = time.perf_counter()
        h = await server._h_vm_health({"vm_id": vmx_path}, _identity_vmx)
        xs_new.append((time.perf_counter() - t0) * 1000)

    o, n = statistics.median(xs_old), statistics.median(xs_new)
    print(f"# bench_composite  vm_health@{Path(vmx_path).name}  N={N}")
    print(f"{'实现':<26}{'中位(ms)':>10}")
    print(f"{'old 串行三探测(0.3.1)':<25}{o:>10.0f}")
    print(f"{'new 并发 vm_health(0.3.2)':<25}{n:>10.0f}")
    print(f"加速比: {o / n:.1f}x（含 ~10ms 本地文件读取，纯探测部分更快）")
    print(f"结果采样: running={h['running']} tools={str(h['tools'])[:40]!r} ip={h['ip']}")
    print(json.dumps({
        "bench": "composite", "n": N, "vm": Path(vmx_path).name,
        "old_serial_ms": round(o), "new_concurrent_ms": round(n),
        "speedup": round(o / n, 1),
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
