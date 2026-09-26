# -*- coding: utf-8 -*-
"""bench_hotpath —— 每调用热路径函数基准（0.3.1 轮3：评估记录）

结论先行（如实）：本轮评估的 2 项候选优化均被基准否决、未进入代码——
  vmx_encryption  尝试"整读+正则定位" → 0.9x（更慢）：文件 I/O ~100µs 主导，
                  encryptionType 通常在前几十行，逐行扫描提前命中即停反而占优。
  decode_output   尝试"ASCII 快速路径" → 0.57-0.75x（更慢）：utf-8 解码对 ASCII 本就
                  近 memcpy 速度，isascii 双趟扫描多一次遍历。
  redact_secrets  未实验即否决：正则 alternation 与顺序 replace 在级联替换边界语义不同，
                  且仅在失败路径执行、replace 是 C 速度——无收益有风险。
  truncate_output 无需改动：O(1) len 比较；env 读取是既有运行时可变契约（测试依赖）。
现行为（= 0.3.0 实现）作为 shipped 基准，attempt_* 为被否决的候选实现。

运行：python benchmark/bench_hotpath.py [N]
"""
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH.parent / "src"))
from vmware_mcp.server import _vmx_encryption, _truncate_output  # noqa: E402
from vmware_mcp.runtime import decode_output, redact_secrets  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 2000


# ---- 被否决的候选实现（0.3.1 轮3 尝试后回滚） ----

def _attempt_regex_vmx(vmx_path):
    import re
    m = re.compile(r"^[^\r\n]*encryptionType[^\r\n]*$", re.M).search(
        open(vmx_path, "r", encoding="utf-8", errors="replace").read())
    if m and "=" in m.group(0):
        return m.group(0).split("=", 1)[1].strip().strip('"')
    return "none"


def _attempt_ascii_decode(data):
    if data.isascii():
        return data.decode("ascii")
    return decode_output(data)


def bench(fn, *args, n=N):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn(*args)
        xs.append((time.perf_counter() - t0) * 1e6)
    return statistics.median(xs)


def make_vmx(path, total_bytes, enc_line=10):
    lines = [f'config.option{i:04d} = "value{i}"' for i in range(200)]
    lines[enc_line] = 'encryptionType = "aes256"'
    content = "\n".join(lines)
    while len(content.encode()) < total_bytes:
        content += "\n" + content
    path.write_text(content[:total_bytes], encoding="utf-8", newline="\n")


def main():
    results = {}
    with tempfile.TemporaryDirectory() as td:
        # ---- _vmx_encryption：等价性 + 速度 ----
        for size in (1024, 4096, 32768):
            vmx = Path(td) / f"v{size}.vmx"
            make_vmx(vmx, size)
            assert _vmx_encryption(str(vmx)) == _attempt_regex_vmx(str(vmx)) == "aes256", size
            o = bench(_vmx_encryption, str(vmx))
            n = bench(_attempt_regex_vmx, str(vmx))
            results[f"vmx_enc_{size}B"] = {"shipped_us": round(o, 2), "attempt_us": round(n, 2), "attempt_speedup": round(o / n, 2)}

        vmx_missing = str(Path(td) / "missing.vmx")
        assert _vmx_encryption(vmx_missing) == "unknown"

        # ---- decode_output：等价性 + 速度 ----
        corpus = {
            "ascii_4K": b"Total running VMs: 2\r\nSome output line here\r\n" * 60,
            "ascii_64K": b"machine-x state=running guid={1234-5678}\r\n" * 1200,
            "gb18030_64K": "虚拟机运行中，进程列表输出测试。".encode("gb18030") * 700,
            "invalid_1K": b"\xff\xfe\xfa" + b"abc" * 340,
        }
        for label, data in corpus.items():
            assert _attempt_ascii_decode(data) == decode_output(data), label
            o = bench(decode_output, data)
            n = bench(_attempt_ascii_decode, data)
            results[f"decode_{label}"] = {"shipped_us": round(o, 2), "attempt_us": round(n, 2), "attempt_speedup": round(o / n, 2)}

    # ---- 其余评估项的量化记录 ----
    results["redact_secrets"] = {"verdict": "rejected_without_trial", "reason": "顺序replace与alternation在级联边界语义不同；仅失败路径执行，无可测收益"}
    results["truncate_output"] = {"verdict": "unchanged", "note": "O(1) len 比较 + env 读取（既有运行时可变契约）", "cost_us": round(bench(_truncate_output, "x" * 30000), 2)}

    print(f"# bench_hotpath  N={N}")
    print(f"{'对象':<16}{'shipped(us)':>12}{'attempt(us)':>12}{'attempt加速':>10}")
    for k, v in results.items():
        if "shipped_us" in v:
            print(f"{k:<16}{v['shipped_us']:>12}{v['attempt_us']:>12}{v['attempt_speedup']:>9}x")
        else:
            print(f"{k:<16}{v.get('verdict','')}")
    print("结论：attempt 均慢于 shipped（speedup<1），否决；shipped 代码保持 0.3.0 原实现。")
    print(json.dumps({"bench": "hotpath", "n": N, **results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
