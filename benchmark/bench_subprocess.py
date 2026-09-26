# -*- coding: utf-8 -*-
"""bench_subprocess —— vmrun 子进程通道并发基准（0.3.1 轮4，真实 VMware Workstation）

测量（全部只读命令，对 VM 无副作用）：
  1. 串行 N 次 `vmrun list` vs 信号量并发（VMWARE_MAX_CONCURRENCY，默认 8）N 次 —— 墙钟加速比
  2. 并发数据隔离（验收项"并发 7 路不串数据"）：16 路并发混合负载——
     8 路 list_running（成功输出）+ 8 路各不存在的独立 vmx（报错输出含各自路径），
     断言每路结果只含自己的标识，无串扰。

需要本机 vmrun.exe；找不到时打印 SKIP 退出 0。
运行：python benchmark/bench_subprocess.py [N]
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

from vmware_mcp.server import get_vmrun  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 16


async def main():
    vmrun = get_vmrun()

    # 预热（进程模板/文件缓存）
    await vmrun.list_running()

    # ---- 1. 串行 vs 信号量并发 ----
    t0 = time.perf_counter()
    for _ in range(N):
        await vmrun.list_running()
    serial_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    results = await asyncio.gather(*(vmrun.list_running() for _ in range(N)))
    par_ms = (time.perf_counter() - t0) * 1000
    assert all(isinstance(r, str) and "Total running VMs" in r for r in results), "并发 list 输出异常"

    single = await vmrun.list_running()
    print(f"# bench_subprocess  vmrun={VMRUN}  N={N}  并发上限={__import__('os').getenv('VMWARE_MAX_CONCURRENCY', '8(默认)')}")
    print(f"单次 list_running 输出: {single.splitlines()[-1] if single else '(空)'}")
    print(f"{'模式':<22}{'墙钟(ms)':>10}{'均值(ms/次)':>12}")
    print(f"{'serial 串行':<21}{serial_ms:>10.0f}{serial_ms / N:>12.0f}")
    print(f"{'parallel 信号量并发':<20}{par_ms:>10.0f}{par_ms / N:>12.0f}")
    print(f"加速比: {serial_ms / par_ms:.1f}x")

    # ---- 2. 并发数据隔离（8 成功 + 8 独立报错） ----
    bogus = [f"D:/vms/bench_nonexistent_{i}.vmx" for i in range(8)]
    tasks = [vmrun.list_running() for _ in range(8)] + [vmrun.check_tools_state(b) for b in bogus]
    mixed = await asyncio.gather(*tasks, return_exceptions=True)
    ok_count = sum(1 for r in mixed[:8] if isinstance(r, str) and "Total running VMs" in str(r))
    isolated = 0
    errs = mixed[8:]
    for b, e in zip(bogus, errs):
        text = str(e).replace("\\", "/")  # vmrun 报错输出为反斜杠路径，归一化后比对
        if b in text and not any(o in text for o in bogus if o != b):
            isolated += 1
    print(f"隔离校验: 成功路 {ok_count}/8 输出纯净；报错路 {isolated}/8 只含各自 vmx 路径")
    print(json.dumps({
        "bench": "subprocess", "n": N,
        "serial_ms": round(serial_ms), "parallel_ms": round(par_ms),
        "speedup": round(serial_ms / par_ms, 1),
        "isolation": f"{ok_count}/8 ok, {isolated}/8 isolated",
        "verdict": "PASS" if ok_count == 8 and isolated == 8 else "FAIL",
    }, ensure_ascii=False))
    sys.exit(0 if ok_count == 8 and isolated == 8 else 1)


if __name__ == "__main__":
    asyncio.run(main())
