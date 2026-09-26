# -*- coding: utf-8 -*-
"""bench_output —— 成功路径序列化尺寸对比（0.3.2 轮7：VMWARE_COMPACT_OUTPUT）

口径：同一批典型成功负载，分别以默认 indent=2（0.3.0-0.3.2 默认，兼容不变）与
      VMWARE_COMPACT_OUTPUT=1 的紧凑形式序列化，比较字符数（token 数与字符数近似成正比）。
红线：默认行为零变化（env 不设时输出与 0.3.1 逐字节一致）；失败路径不受影响。

运行：python benchmark/bench_output.py
"""
import json
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH.parent / "src"))
from vmware_mcp.server import _dumps  # noqa: E402

VM_LIST = [{"id": f"vm-{i:02d}", "path": f"D:/vms/machine-{i:02d}/windows-11-pro.vmx"} for i in range(30)]
VM_PS = {"processes": [
    {"pid": 1000 + i, "img": "C:\\Program Files\\app\\service.exe", "cmd": "--mode=worker --port=8443", "user": "SYSTEM"}
    for i in range(60)
]}
VM_HEALTH = {
    "vm_id": "D:/vms/win11.vmx", "vmx": "D:/vms/win11.vmx", "encryptionType": "none",
    "running": True, "tools": "running", "ip": "192.168.240.129",
    "suspend_artifacts": {"vmem": [], "vmss": []},
    "log_tail": "\n".join(f"vmx| I{1250000 + i:06d}: VMXVmxDBGetVmxStats: perf {i} values" for i in range(20)),
}
SNAP_LIST = {"snapshots": [{"name": f"snap-{i}", "id": f"snapshot-{i}", "description": f"自动快照 {i} 号", "created": "2026-09-26T01:00:00"} for i in range(15)]}


def main():
    print(f"# bench_output  成功路径序列化尺寸（字符数；token 数近似成正比）")
    print(f"{'负载':<16}{'indent=2(默认)':>16}{'compact':>10}{'节省':>8}")
    total_p, total_c = 0, 0
    for label, payload in (("vm_list×30", VM_LIST), ("vmrun_ps×60", VM_PS), ("vm_health", VM_HEALTH), ("snapshot_list×15", SNAP_LIST)):
        assert json.loads(_dumps_compact(payload)) == json.loads(_dumps(payload)), label
        p, c = len(_dumps(payload).encode("utf-8")), len(_dumps_compact(payload).encode("utf-8"))
        total_p += p
        total_c += c
        print(f"{label:<16}{p:>16}{c:>10}{(1 - c / p) * 100:>7.0f}%")
    print(f"{'合计':<16}{total_p:>16}{total_c:>10}{(1 - total_c / total_p) * 100:>7.0f}%")
    print(json.dumps({
        "bench": "output",
        "cases": {k: {"indent2_bytes": len(_dumps(v).encode()), "compact_bytes": len(_dumps_compact(v).encode())} for k, v in (("vm_list", VM_LIST), ("vm_ps", VM_PS), ("vm_health", VM_HEALTH), ("snapshots", SNAP_LIST))},
        "total_saved_pct": round((1 - total_c / total_p) * 100),
    }, ensure_ascii=False))


def _dumps_compact(payload):
    import os
    os.environ["VMWARE_COMPACT_OUTPUT"] = "1"
    try:
        return _dumps(payload)
    finally:
        os.environ.pop("VMWARE_COMPACT_OUTPUT")


if __name__ == "__main__":
    main()
