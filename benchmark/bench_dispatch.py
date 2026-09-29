# -*- coding: utf-8 -*-
"""bench_dispatch —— call_tool 分发机制性能基准（0.3.1 轮2：if/elif 链 → 路由表）

口径：只测分发机制本身（分支函数体两种设计相同，成本抵消）。
  old = v0.3.0 O(n) 顺序比较：按原分支求值顺序（SERVER 5 → 主链 130，两条链都走全）逐个 name == cand
  new = 0.3.1 O(1)：dict 查表 + 预编译参数提取器 + 适配器解析（适配器/vmx 打桩，无 I/O）
附端到端参考（真实 call_tool，护栏+序列化+桩业务体），排除 screenshot_ocr（每次真实加载 OCR 引擎）。

运行：python benchmark/bench_dispatch.py [N]
"""
import asyncio
import json
import logging
import statistics
import sys
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH.parent / "src"))
sys.path.insert(0, str(BENCH))
from vmware_mcp import server  # noqa: E402
from verify_dispatch_equivalence import gen_args, _Recorder  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200

logging.getLogger("vmware_mcp").setLevel(logging.ERROR)  # 静化基准期间的告警行

# v0.3.0 原 if/elif 求值顺序（重构前实录）
OLD_ORDER = [
    "set_vm_encryption_password", "vm_resolve", "vm_health", "vm_log_tail", "screenshot_ocr",
    "vm_list", "vm_get", "vm_create", "vm_delete", "vm_update", "vm_power_get", "vm_power_set",
    "vm_nic_list", "vm_nic_create", "vm_nic_delete", "vm_ip_get", "vm_folder_list", "vm_folder_create",
    "vm_folder_delete", "network_list", "network_create", "network_portforward_list",
    "network_portforward_set", "network_portforward_delete",
    "vmrun_list", "vmrun_clone", "vmrun_upgrade", "vmrun_delete", "vmrun_start", "vmrun_stop",
    "vmrun_reset", "vmrun_suspend", "vmrun_pause", "vmrun_unpause", "vmrun_snapshot_list",
    "vmrun_snapshot_take", "vmrun_snapshot_delete", "vmrun_snapshot_revert", "vmrun_file_exists",
    "vmrun_dir_exists", "vmrun_ls", "vmrun_mkdir", "vmrun_rmdir", "vmrun_rm", "vmrun_rename",
    "vmrun_copy_to", "vmrun_copy_from", "vmrun_temp_file", "vmrun_copy_dir_to", "vmrun_copy_dir_from",
    "vmrun_run", "vmrun_script", "vmrun_ps", "vmrun_kill", "vmrun_shared_enable", "vmrun_shared_disable",
    "vmrun_shared_add", "vmrun_shared_remove", "vmrun_shared_set", "vmrun_device_connect",
    "vmrun_device_disconnect", "vmrun_var_read", "vmrun_var_write", "vmrun_screenshot",
    "vmrun_keystrokes", "vmrun_tools_install", "vmrun_tools_state", "vmrun_guest_ip",
    "vmrun_host_networks", "vmrun_portforward_list", "vmrun_portforward_set", "vmrun_portforward_delete",
    "snapshot_list", "snapshot_take", "snapshot_revert", "snapshot_delete", "snapshot_clone",
    "guest_run", "guest_ps", "guest_kill", "guest_ls", "guest_mkdir", "guest_rm", "guest_rmdir",
    "guest_copy_to", "guest_copy_from", "guest_env", "mks_screenshot", "mks_send_key", "mks_query",
    "chipset_query", "chipset_set_cpu", "chipset_set_memory", "chipset_set_cores", "tools_query",
    "tools_install", "tools_upgrade", "template_create", "template_deploy", "disk_query",
    "disk_create", "disk_extend", "config_query", "config_set", "power_query", "power_start",
    "power_stop", "power_pause", "power_unpause", "power_reset", "power_suspend", "ethernet_query",
    "ethernet_set_type", "ethernet_set_present", "ethernet_set_connected", "ethernet_set_device",
    "ethernet_set_network", "ethernet_purge", "hgfs_query", "hgfs_set_enabled", "hgfs_set_path",
    "hgfs_set_name", "hgfs_set_read", "hgfs_set_write", "serial_query", "serial_set_present",
    "serial_purge", "sata_query", "sata_set_present", "sata_purge", "nvme_query", "nvme_set_present",
    "nvme_purge", "vprobes_query", "vprobes_enable", "vprobes_load", "vprobes_reset",
]


def old_scan(name):
    hit = 0
    for cand in OLD_ORDER[:5]:
        hit += 1
        if name == cand:
            return hit
    for cand in OLD_ORDER:
        hit += 1
        if name == cand:
            return hit
    return hit


async def _identity_vmx(vm_id):
    return vm_id


# 15 个自定义逻辑分支：函数体与重构前逐字相同（成本抵消），纯分发口径下两侧都只测查表本身
SPECIALS = {
    "set_vm_encryption_password", "vm_resolve", "vm_health", "vm_log_tail", "screenshot_ocr",
    "vm_list", "vm_create", "vm_update", "vm_nic_create", "vm_folder_create",
    "network_create", "network_portforward_set",
    "vmrun_run_job", "vmrun_read_file", "vmrun_wait_file",
}


async def main():
    tools = await server.list_tools()
    names = [t.name for t in tools]
    schemas = {t.name: t.inputSchema for t in tools}

    server._client = _Recorder("c")
    server._vmrun = _Recorder("r")
    server._vmcli = _Recorder("l")

    # ---- 纯分发机制对比 ----
    xs_old, xs_new = {}, {}
    for name in names:
        args = gen_args(schemas[name])
        handler = server._HANDLERS[name]
        o, n = [], []
        for _ in range(N):
            t0 = time.perf_counter()
            old_scan(name)
            o.append((time.perf_counter() - t0) * 1e6)
            t0 = time.perf_counter()
            h = server._HANDLERS.get(name)
            if name not in SPECIALS:
                await h(args, _identity_vmx)
            n.append((time.perf_counter() - t0) * 1e6)
        xs_old[name], xs_new[name] = statistics.median(o), statistics.median(n)

    # ---- 端到端参考（排除 OCR 真实引擎分支） ----
    e2e = []
    for name in names:
        if name == "screenshot_ocr":
            continue
        args = gen_args(schemas[name])
        xs = []
        for _ in range(N):
            t0 = time.perf_counter()
            await server.call_tool(name, dict(args))
            xs.append((time.perf_counter() - t0) * 1e6)
        e2e.append(statistics.median(xs))

    mean_old = statistics.mean(xs_old.values())
    mean_new = statistics.mean(xs_new.values())
    worst_old = max(xs_old, key=xs_old.get)
    worst_new = max(xs_new, key=xs_new.get)

    print(f"# bench_dispatch  N={N}/tool")
    print(f"{'指标':<34}{'old(链扫描)':>14}{'new(路由表)':>14}")
    print(f"{'纯分发中位(us,全工具均值)':<33}{mean_old:>14.2f}{mean_new:>14.2f}")
    print(f"{'纯分发最差工具(us)':<33}{xs_old[worst_old]:>14.2f}{xs_new[worst_new]:>14.2f}  ({worst_old}/{worst_new})")
    print(f"{'比较次数(首/尾工具)':<33}{'1/135':>14}{'O(1)':>14}")
    print(f"端到端参考(new,排除OCR): 中位 {statistics.median(e2e):.1f} us, P95 {sorted(e2e)[int(len(e2e)*0.95)]:.1f} us")
    print("结论（如实）：分发两侧均为 µs 级、非瓶颈（真实调用为百 ms 级子进程/REST 等待）；"
          "路由表化的收益是架构性——圈复杂度 136→约10、逐分支可测、收口分析报告发现3，性能中性。")
    print(json.dumps({
        "bench": "dispatch", "n": N,
        "old_pure_mean_us": round(mean_old, 2), "old_pure_worst_us": round(xs_old[worst_old], 2),
        "new_pure_mean_us": round(mean_new, 2), "new_pure_worst_us": round(xs_new[worst_new], 2),
        "new_e2e_median_us": round(statistics.median(e2e), 1),
        "new_e2e_p95_us": round(sorted(e2e)[int(len(e2e) * 0.95)], 1),
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
