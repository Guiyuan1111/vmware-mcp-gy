# -*- coding: utf-8 -*-
"""bench_list_tools —— tools/list 路径性能基准（0.3.1 轮1：构建缓存）

对比：
  old = v0.3.0 行为：每次 tools/list 重建 137 个 Tool 定义 + 注入循环（_build_tools 直调）
  new = 0.3.1 行为：进程内缓存，首次构建后直接返回（list_tools）

正确性红线：两者序列化输出必须字节级一致（含 annotations/注入参数）。
运行：python benchmark/bench_list_tools.py [N]
"""
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vmware_mcp import server  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 300


def serialize(tools) -> str:
    return json.dumps([t.model_dump() for t in tools], ensure_ascii=False, sort_keys=True)


def bench_sync(fn, n):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1e6)
    return xs


async def bench_async(fn, n):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        await fn()
        xs.append((time.perf_counter() - t0) * 1e6)
    return xs


def report(label, xs):
    return {
        "path_us_median": round(statistics.median(xs), 1),
        "path_us_p95": round(sorted(xs)[int(len(xs) * 0.95)], 1),
        "path_us_mean": round(statistics.mean(xs), 1),
    }


async def main():
    # 等价性断言（红线）：old 每次重建 vs new 缓存，序列化输出字节一致
    old_tools = server._build_tools()
    new_tools = await server.list_tools()
    old_ser, new_ser = serialize(old_tools), serialize(new_tools)
    assert old_ser == new_ser, "输出不一致：缓存化改变了 tools/list 结果"
    assert new_tools is await server.list_tools(), "缓存未命中"

    payload_kb = len(new_ser.encode("utf-8")) / 1024

    old_xs = bench_sync(server._build_tools, N)
    await server.list_tools()  # 预热缓存
    new_xs = await bench_async(server.list_tools, N)

    o, n = report("old", old_xs), report("new", new_xs)
    speedup = o["path_us_median"] / n["path_us_median"]

    print(f"# bench_list_tools  N={N}")
    print(f"payload: {payload_kb:.1f} KB / 137 tools（单次序列化体积，两种实现相同）")
    print(f"{'实现':<28}{'中位(us)':>10}{'P95(us)':>10}{'均值(us)':>10}")
    print(f"{'old 每次重建(v0.3.0)':<26}{o['path_us_median']:>10}{o['path_us_p95']:>10}{o['path_us_mean']:>10}")
    print(f"{'new 缓存命中(0.3.1)':<27}{n['path_us_median']:>10}{n['path_us_p95']:>10}{n['path_us_mean']:>10}")
    print(f"speedup: {speedup:.0f}x")
    print(json.dumps({"bench": "list_tools", "n": N, "payload_kb": round(payload_kb, 1), "old": o, "new": n, "speedup": round(speedup, 1)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
