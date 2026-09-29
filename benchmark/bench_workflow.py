# -*- coding: utf-8 -*-
"""bench_workflow —— 工作流组合工具（v0.4.0）真机量化基准

量化 0.4.0 三个组合工具相对旧工作流形态的收益。历史会话统计（647 次调用）
表明最大摩擦不在单次毫秒而在「往返圈数」：
  - copy_to→run→copy_from 三连圈 68 个（3 次工具调用 / 3 个模型回合）
  - run/script 输出 ≤50B 占 106/124（stdout 通道闲置，结果走文件回拷）
  - copy_from 140 次为最高频工具，多数仅为取回小文本再本地 Read

对比口径（同一真实 VM 上交替执行，各 N 轮取中位）：
  A. run_job  vs  legacy(copy_to + run bash -c 重定向 + copy_from)
     —— 工具侧 wall 时间、调用数 3→1、模型回合 3→1、结果字段（stdout/exit_code）
  B. read_file vs  legacy(copy_from + 宿主读取)
  C. no_wait 链路：run_job(no_wait) + wait_file + read_file 的端到端正确性
  D. wait_file 错凭据 fail-fast（对照：无限重试至超时）

用法（需一台已装 Tools 的运行中 Linux VM 与 guest 凭据）：
  set VMWARE_BENCH_VMX=<vmx 路径>
  set VMWARE_BENCH_USER=<guest 用户>
  set VMWARE_BENCH_PASS=<guest 密码>
  python benchmark/bench_workflow.py [N]
"""
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vmware_mcp import server  # noqa: E402

VMX = os.getenv("VMWARE_BENCH_VMX", "")
USER = os.getenv("VMWARE_BENCH_USER", "")
PASS = os.getenv("VMWARE_BENCH_PASS", "")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 4
U = {"user": USER, "password": PASS}

# 该目录存放 legacy 形态的中转文件；结束后清理
WORKDIR = Path(__file__).resolve().parent / "_bench_wf_tmp"


async def timed(coro):
    t0 = time.perf_counter()
    r = await coro
    return time.perf_counter() - t0, r[0].text


async def legacy_triple(script_text: str) -> float:
    """旧工作流形态：上传→(bash -c 重定向执行)→回拷。返回 wall 秒。"""
    host_script = WORKDIR / "bench-legacy.sh"
    host_out = WORKDIR / "bench-legacy.out"
    host_script.write_text(script_text + "\n", newline="\n")
    t0 = time.perf_counter()
    await server.call_tool("vmrun_copy_to", {"vm_id": VMX, "host_path": str(host_script), "guest_path": "/tmp/bench-legacy.sh", **U})
    await server.call_tool("vmrun_run", {"vm_id": VMX, "program": "/bin/bash", "args": ["-c", ". /tmp/bench-legacy.sh > /tmp/bench-legacy.out 2>&1"], **U})
    await server.call_tool("vmrun_copy_from", {"vm_id": VMX, "guest_path": "/tmp/bench-legacy.out", "host_path": str(host_out), **U})
    return time.perf_counter() - t0


async def main():
    if not (VMX and USER):
        print("需要 VMWARE_BENCH_VMX / VMWARE_BENCH_USER / VMWARE_BENCH_PASS 环境变量")
        sys.exit(2)
    WORKDIR.mkdir(exist_ok=True)
    SCRIPT = "echo bench-$(date +%s%N)"
    print(f"# bench_workflow  N={N}  vmx={os.path.basename(VMX)}")

    leg, job = [], []
    for i in range(N):
        dt_l = await legacy_triple(SCRIPT)
        dt_j, _ = await timed(server.call_tool("vmrun_run_job", {"vm_id": VMX, "script": SCRIPT, **U}))
        leg.append(dt_l)
        job.append(dt_j)
        print(f"round{i}: legacy(3 calls)={dt_l:.2f}s run_job(1 call)={dt_j:.2f}s")
    print(f"A. run_job vs legacy: wall {statistics.median(leg):.2f}s -> {statistics.median(job):.2f}s"
          f"（含 2 笔并发清理），工具调用 3->1，模型回合 3->1，新增 stdout+exit_code")

    rd, cp = [], []
    for i in range(N):
        dt_r, txt = await timed(server.call_tool("vmrun_read_file", {"vm_id": VMX, "path": "/tmp/bench-legacy.out", **U}))
        t0 = time.perf_counter()
        await server.call_tool("vmrun_copy_from", {"vm_id": VMX, "guest_path": "/tmp/bench-legacy.out", "host_path": str(WORKDIR / "bench-cp.out"), **U})
        (WORKDIR / "bench-cp.out").read_bytes()
        dt_c = time.perf_counter() - t0
        rd.append(dt_r)
        cp.append(dt_c)
        print(f"round{i}: read_file(1 call)={dt_r:.2f}s copy_from(1 call, 再加本地 Read 回合)={dt_c:.2f}s")
    print(f"B. read_file vs copy_from: wall {statistics.median(cp):.2f}s -> {statistics.median(rd):.2f}s，"
          f"且省掉本地 Read 的整个模型回合")

    # C. no_wait 链路正确性（长任务 + 非零退出码）
    t0 = time.perf_counter()
    _, txt = await timed(server.call_tool("vmrun_run_job", {"vm_id": VMX, "script": "sleep 2; echo chain-ok; exit 7", "no_wait": True, **U}))
    job_info = json.loads(txt)
    _, txt = await timed(server.call_tool("vmrun_wait_file", {"vm_id": VMX, "path": job_info["output_guest_path"], "timeout_s": 25, "interval_s": 2, **U}))
    wf = json.loads(txt)
    _, txt = await timed(server.call_tool("vmrun_read_file", {"vm_id": VMX, "path": job_info["output_guest_path"], **U}))
    content = json.loads(txt).get("content", "")
    total = time.perf_counter() - t0
    ok = wf["exists"] and "chain-ok" in content and "__JOB_RC=7" in content
    print(f"C. no_wait 链路（3 次调用收长任务+非零码）: {total:.2f}s exists={wf['exists']} content_ok={ok}")

    # D. 错凭据 fail-fast
    t0 = time.perf_counter()
    await server.call_tool("vmrun_wait_file", {"vm_id": VMX, "path": "/tmp/bench-x.out", "timeout_s": 15,
                                               "user": USER, "password": "definitely-wrong"})
    print(f"D. wait_file 错凭据 fail-fast: {time.perf_counter() - t0:.2f}s（对照：凭据类错误反复重试直至 15s 超时）")

    # 清理 guest + 本地
    for p in ("/tmp/bench-legacy.sh", "/tmp/bench-legacy.out", "/tmp/bench-x.out"):
        try:
            await server.call_tool("vmrun_rm", {"vm_id": VMX, "path": p, "confirm": True, **U})
        except Exception:
            pass
    for f in WORKDIR.iterdir():
        f.unlink()
    WORKDIR.rmdir()
    print("cleanup: guest 与本地中转文件已清理")


if __name__ == "__main__":
    asyncio.run(main())
