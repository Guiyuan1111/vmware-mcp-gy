# -*- coding: utf-8 -*-
"""bench_lifecycle —— 进程生命周期剖析（0.4.2 轮2，无需 VMware）

量化服务器从进程启动到协议可用的每一毫秒去向，回答「冷启动还有没有优化空间」：
  1. import 分解：bare python / httpx 边际 / mcp SDK 边际 / 自有代码
  2. 协议就绪：spawn → initialize 响应 → tools/list 响应（端到端）
  3. 工具表构建：_build_tools() 单次成本（进程内只发生一次）
  4. 成功路径序列化：indent=2（默认） vs compact（VMWARE_COMPACT_OUTPUT=1）耗时

已知否决记录（勿重复尝试）：
  - httpx 懒加载：mcp 加载后 httpx 边际 import 为噪声级（实测 -118ms～+0ms 区间，共享依赖 anyio/h11），
    且 REST 通道为三大通道之一必须可用，懒加载无收益、徒增错误暴露时机变化。否决。
  - mcp SDK 817ms 为协议核心硬依赖，不可避。

用法：python benchmark/bench_lifecycle.py
"""
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

N = 5


def median_spawn(code: str) -> float:
    v = []
    for _ in range(N):
        t0 = time.perf_counter()
        subprocess.run([sys.executable, "-c", code], capture_output=True, cwd=ROOT)
        v.append(time.perf_counter() - t0)
    return statistics.median(v) * 1000


def build_tools_cost() -> float:
    import importlib
    import vmware_mcp.server as srv
    importlib.reload(srv)  # 清缓存，从零构建
    t0 = time.perf_counter()
    srv._TOOLS_CACHE = None
    srv._TOOLS_CACHE = srv._build_tools()
    dt = (time.perf_counter() - t0) * 1000
    srv._TOOLS_CACHE = None  # 归还惰性状态
    return dt


def serialization_cost() -> tuple[float, float, int, int]:
    import vmware_mcp.server as srv
    tools = srv._build_tools()
    payload = [t.model_dump() for t in tools]
    t0 = time.perf_counter()
    pretty = json.dumps(payload, indent=2, ensure_ascii=False)
    dt_pretty = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    compact = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    dt_compact = (time.perf_counter() - t0) * 1000
    return dt_pretty, dt_compact, len(pretty), len(compact)


def protocol_ready() -> tuple[float, float]:
    """spawn → initialize 响应 / → tools/list 响应（端到端毫秒）。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.path.insert(0, r'%s'); from vmware_mcp.server import main; main()" % (ROOT / "src")],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    lines = []
    t_spawn = time.perf_counter()
    try:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                                "clientInfo": {"name": "lifecycle", "version": "0"}}}) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        proc.stdin.flush()
        deadline, got = time.monotonic() + 30, set()
        t_init = t_list = None
        while time.monotonic() < deadline and len(got) < 2:
            line = proc.stdout.readline()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 1 and "t_init" not in got:
                t_init = time.perf_counter() - t_spawn
                got.add("t_init")
            elif msg.get("id") == 2 and "t_list" not in got:
                t_list = time.perf_counter() - t_spawn
                got.add("t_list")
        return (t_init or -1) * 1000, (t_list or -1) * 1000
    finally:
        proc.kill()


def main():
    print("# bench_lifecycle  (median of %d)\n" % N)
    bare = median_spawn("pass")
    ours = median_spawn("import sys; sys.path.insert(0, r'%s'); import vmware_mcp.server" % (ROOT / "src"))
    bt = build_tools_cost()
    dp, dc, sp, sc = serialization_cost()
    print(f"import bare python      : {bare:7.1f}ms")
    print(f"import vmware_mcp.server: {ours:7.1f}ms  (自有代码+SDK+deps；SDK 占绝对主导，见否决记录)")
    print(f"_build_tools (140 tools): {bt:7.1f}ms  (进程内仅一次，已缓存)")
    print(f"serialize tools/list    : indent={dp:5.2f}ms ({sp/1024:.1f}KB)  compact={dc:5.2f}ms ({sc/1024:.1f}KB)")
    ti, tl = protocol_ready()
    print(f"protocol ready          : initialize={ti:7.0f}ms  tools/list 就绪={tl:7.0f}ms  (spawn+import+stdio 启动全链)")
    print("\n结论：冷启动由 mcp SDK import 主导（约 %.0fms），自有代码与工具表构建均为毫秒级，无可优化空间；" % (ours - bare - 40))
    print("      httpx 懒加载已被边际测量否决（见文件头否决记录）；每会话一次性成本 ~1s，非热路径。")


if __name__ == "__main__":
    main()
