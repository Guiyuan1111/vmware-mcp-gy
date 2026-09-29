# -*- coding: utf-8 -*-
"""bench_health_channels —— vm_health 双通道基准（0.4.2 轮1，真实 VMware）

对比 vm_health 三探测的两种通道形态（同一台 VM 天然对照）：
  old = vmx 直通形态（running/tools/ip 全部孵化 vmrun 子进程 ×3）
  new = REST vm_id 形态（running/ip 走 vmrest HTTP 共享池，tools 恒走 vmrun；REST 不可达时逐字段回退）

另测 401 环境下 REST 形态的回退正确性与回退开销（对照红线：回退后字段语义不变）。

用法（需 vmrest 凭据；VM 不要求运行，但口径注明）：
  set VMWARE_USERNAME=... & set VMWARE_PASSWORD=...
  python benchmark/bench_health_channels.py [N]
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

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5


async def pick_vm():
    vms = await server.get_client().list_vms()
    return vms[0]["id"], vms[0]["path"]  # 同时拿到 REST id 与 vmx 路径


async def timed_health(vm_id):
    t0 = time.perf_counter()
    r = await server.call_tool("vm_health", {"vm_id": vm_id})
    return time.perf_counter() - t0, r[0].text


async def main():
    rest_id, vmx_path = await pick_vm()
    print(f"# bench_health_channels  N={N}  vm={Path(vmx_path).name}")
    print(f"# 口径：VM 电源状态不限定（tools 探测对关机 VM 快速失败，仅影响绝对值不影响通道对比）")

    old, new, fields = [], [], None
    for i in range(N):
        dt_old, txt_old = await timed_health(vmx_path)   # 旧形态 = 全 vmrun
        dt_new, txt_new = await timed_health(rest_id)    # 新形态 = REST 快路径
        old.append(dt_old)
        new.append(dt_new)
        j_old, j_new = json.loads(txt_old), json.loads(txt_new)
        fields = (j_old["running"], j_new["running"], j_old["ip"], j_new["ip"])
        print(f"round{i}: old(vmx→3×vmrun)={dt_old:.2f}s  new(rest→HTTP+1×vmrun)={dt_new:.2f}s  fields_old/new=({fields[0]},{fields[1]})")
    med_old, med_new = statistics.median(old), statistics.median(new)
    print(f"MEDIAN old={med_old*1000:.0f}ms new={med_new*1000:.0f}ms  speedup={med_old/med_new:.2f}x")

    # 回退正确性：断开凭据（空 auth 强制 401），REST 形态应回退 vmrun 且字段语义不变
    real_client = server._client
    try:
        class _Denied:
            async def get_power_state(self, vm_id):
                raise RuntimeError("simulated 401")

            async def get_vm_ip(self, vm_id):
                raise RuntimeError("simulated 401")

        server._client = _Denied()
        dt_fb, txt_fb = await timed_health(rest_id)
        j_fb = json.loads(txt_fb)
        same = (j_fb["running"] == j_old["running"]) if "running" in j_fb else False
        print(f"FALLBACK(sim-401): {dt_fb*1000:.0f}ms  running={j_fb.get('running')}  与 old 形态一致={same}")
    finally:
        server._client = real_client

    print(f"SUMMARY_JSON {json.dumps({'old_ms': round(med_old*1000), 'new_ms': round(med_new*1000), 'speedup': round(med_old/med_new, 2), 'fields_equivalent': fields[0] == fields[1]})}")


if __name__ == "__main__":
    asyncio.run(main())
