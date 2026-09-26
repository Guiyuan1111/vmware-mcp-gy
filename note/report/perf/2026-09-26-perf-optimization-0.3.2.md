# vmware-mcp-gy 性能优化对比报告（v0.3.2，2026-09-26）

承接 [0.3.1 报告](2026-09-26-perf-optimization-0.3.1.md)（Python 侧已达 µs 级后的结论：剩余空间在 I/O 编排层与 token 层）。本报告量化 0.3.2 的两轮优化（轮6 组合工具内部并发化 / 轮7 紧凑输出），基准程序位于 [`benchmark/`](../../../../benchmark/)。

背景依据：对比业界做法的调研结论（2026-09-26 会话）——vmrun 每调用付一次 Windows 进程孵化（本机 ~300ms），vmrest 常驻 HTTP 仅 ~1ms（[GNS3 #1416](https://github.com/GNS3/gns3-server/issues/1416) 即此思路；[govc session.login](https://github.com/vmware/govmomi/blob/main/govc/USAGE.md) 用会话摊薄登录是同型问题）；大工具表场景业界以 Tool Search / deferred loading 降 token（宿主侧功能）。

## 轮6：组合工具内部并发化（P1）

`vm_health` 内部 3 个互不依赖的只读探测（list_running / check_tools_state / get_guest_ip）由串行改为 `asyncio.gather` 并发；`vm_resolve` 多匹配的电源状态查询同理。

红线保障：

- 结果逐字段一致（含错误分支语义：running→`unknown:`、tools→`error:`、ip→`None`）——专项单测对照**旧串行参照实现**锁定；
- 调用序列不变（gather 保序）——黄金快照门 138/138 逐字节全绿；
- 并发上限不受破坏——探测仍经 `vmrun._run` 的全局信号量（`VMWARE_MAX_CONCURRENCY`）。

| 指标（真实 vmrun，Ubuntu 64 位.vmx，N=3 中位） | 0.3.1 串行 | 0.3.2 并发 | 提升 |
|---|---|---|---|
| vm_health 三探测总耗时 | 1021 ms | 421 ms | **2.4x** |

复现：`python benchmark/bench_composite.py`。`vm_resolve` 每多一个匹配省一次 ~300ms 串行等待（2 匹配实测并发后总耗时 ≈ 单次查询时长）。

## 轮7：紧凑输出（P3）

新增 `VMWARE_COMPACT_OUTPUT=1`：成功路径 JSON 以紧凑形式（无缩进/无空格分隔）序列化；**默认关闭，输出与 0.3.1 逐字节一致**（兼容红线）；失败路径（`_error_content`）不受影响，保持缩进便于人工排查。

| 负载 | indent=2（默认） | compact | 节省 |
|---|---|---|---|
| vm_list ×30 VM | 2372 B | 1831 B | 23% |
| vmrun_ps ×60 进程 | 8963 B | 6435 B | 28% |
| vm_health | 1265 B | 1217 B | 4% |
| snapshot_list ×15 | 2213 B | 1575 B | 29% |
| **合计** | **14813 B** | **11058 B** | **25%** |

字符数与 token 数近似成正比——对列表类高产量负载，模型上下文占用约省 1/4。复现：`python benchmark/bench_output.py`。

## 评估后不做（本期）

- **REST 优先替代重叠 vmrun 操作（P2，最高单项上限 ~10x）**：设计上需 vmrest 凭据，本机 vmrest 未配置凭据（401），按"无实测依据不上线"纪律**暂缓**，待凭据策略确定后实施（REST 失败自动回退 vmrun 的方案已论证可行）。
- **Tool Search / deferred loading（P4）**：宿主侧功能，服务端无实现点；137 工具的 84.9 KB 工具表 payload 可在支持该特性的宿主中获得收益，README 不另作声明。
- **listChanged 通知**：工具集运行期静态，无意义。
- **orjson / to_thread 化文件读**：序列化与文件读均在 µs 级（0.3.1 已证），不值得新增依赖或复杂度。

## 红线门

| 门 | 结果 |
|----|------|
| 单元测试 | 52/52（47 + 5 项轮6/7 新增） |
| 黄金快照 | 138/138 逐字节一致（vm_health/vm_resolve 调用序列与结果不变） |
| 工具面 / 护栏 | 137 工具零变化；READ_ONLY/confirm/annotations 行为不变 |
| 兼容性 | `VMWARE_COMPACT_OUTPUT` 不设时输出与 0.3.1 逐字节一致 |
