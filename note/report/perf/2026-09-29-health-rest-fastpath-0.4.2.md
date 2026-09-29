# 0.4.2 双轮剖析：vm_health 通道选择 + 进程生命周期

日期：2026-09-29 ｜ 环境：Windows 11 + VMware Workstation（RHEL-10）+ vmrest 凭据可用

延续 0.3.x（毫秒级通道）/0.4.x（回合级与作用域级）的方法论：每轮先测量、数据定案、收益不足即否决并记录。本轮两轮各对一个此前未测量过的维度做剖析。

## 轮 1：vm_health 通道选择（REST 快路径）

**假设**：vm_health 三探测每次孵化 3 个 vmrun 子进程（~650-950ms）；REST 通道实测快 ~10x（0.3.1 结论），把探测迁 REST 应有大幅收益。

**实施**：REST vm_id 形态下 `running` 走 `get_power_state`（纯元数据），等价映射 poweredOn→true；REST 不可达/401 逐字段回退 vmrun；vmx 直通形态维持全 vmrun。字段值语义与旧实现逐字段一致。

**测量打脸与收缩**（`benchmark/bench_health_channels.py`，N=5 中位）：
- 初版把 `ip` 也走 REST——实测 vmrest `/ip` 底层同为 VIX 且**对关机 VM 返回 409**（触发回退），新旧打平（1.04x）。收缩为仅 `running` 走 REST 后：**old 956ms → new 789ms，1.21x**。
- 收益薄的原因：总时长由 `tools` 探测（无 REST 端点）主导，并发下 `max()` 遮蔽 running 的节省。

**保留理由**（非速度）：① 字段等价 + 401 模拟回退验证通过，零风险；② `VMWARE_TOOLS=rest` 作用域下（vmrun.exe 缺失/被裁场景）`running` 仍可诊断——可用性价值；③ 与 `vm_resolve` 的降级模式代码同构，复杂度增量 ~20 行。

## 轮 2：进程生命周期剖析（`benchmark/bench_lifecycle.py`）

| 维度 | 实测 | 结论 |
| --- | --- | --- |
| import bare python | 58ms | 基线 |
| import vmware_mcp.server | 854ms | **mcp SDK 占 ~756ms**，自有代码毫秒级 |
| `_build_tools`（140 工具） | 0.6ms | 进程内一次，已缓存 |
| tools/list 序列化 | indent 0.62ms / compact 0.48ms | 微秒级，非热点 |
| 协议就绪（spawn→init→list） | 896 / 900ms | 每会话一次性成本，非热路径 |

**否决记录**（防重复尝试）：
1. **httpx 懒加载**：mcp 加载后 httpx 边际 import 为噪声级（-118ms～0，共享 anyio/h11），REST 为三大通道之一必须常驻——无收益、徒增错误暴露时机变化。否决。
2. **作用域过滤结果缓存**：140 项过滤 ~5µs、每会话一次——零收益，不做。

## 口径更正（0.4.1 报告）

tools/list 负载的 74.8KB 为**紧凑 JSON 字节**；SDK 实发 indent=2 形态为 **104.5KB**。作用域相对节省比例不变（vmrun ≈48%→indent 口径 ≈55%），绝对值以本报告为准。

## 回归

单元测试 74/74（新增 REST 快路径 4 项：等价映射、poweredOff、401 回退、vmx 形态不变）；黄金快照 141/141 逐字节一致（vmx 形态基准路径未变，无需重录）；协议符合性门 9/9。

## 结论

0.3.x-0.4.x 累计六轮后，可测优化空间已系统性收敛：单工具毫秒级（0.3.x）、回合级（0.4.0）、工具面级（0.4.1）均已覆盖；本轮两个新维度（通道选择、生命周期）实测分别为 1.21x（保留，主因可用性）与无空间（如实落档）。剩余大头均属机制性成本：mcp SDK import（~756ms/会话一次）、vmrun 进程孵化（VIX 固有）、tools 探测无 REST 端点。若无新硬件/新 SDK 版本，性能线建议封版。
