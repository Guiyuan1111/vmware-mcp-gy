# note/ 文档索引

| 目录 | 内容 | 维护约定 |
| --- | --- | --- |
| [`release/`](release/) | 每版本改动记录：`<版本号>.md`，与 git tag 一一对应 | 每次发版必写 |
| [`report/perf/`](report/perf/) | 性能对比报告（基准数据、实测记录） | 每轮性能改动必写 |
| [`research/`](research/) | 外部调研笔记（快照性质，注明调研日期） | 按需 |
| [`codebase-analyzer/`](codebase-analyzer/) | codebase-analyzer 生成的架构分析报告（带时间戳的快照，不随版本更新；头部有时效标注） | 重大重构后可重跑 |

## 版本线（截至 0.4.1，2026-09-29）

- **0.4.1** 工具面作用域：`VMWARE_TOOLS`（rest/vmrun/vmcli/core）裁剪暴露面；`benchmark/protocol_conformance.py` 协议符合性门 9 项。报告：`report/perf/2026-09-29-toolset-scope-0.4.1.md`
- **0.4.0** 工作流组合工具：`vmrun_run_job` / `vmrun_read_file` / `vmrun_wait_file`（合并高频多连调用，省模型回合）。报告：`report/perf/2026-09-29-workflow-composite-0.4.0.md`
- **0.3.2** 性能第二期：vm_health/vm_resolve 并发化、`VMWARE_COMPACT_OUTPUT`
- **0.3.1** 性能第一期：tools/list 缓存、路由表分发、子进程并发、REST 连接池
- **0.3.0** 安全护栏：`VMWARE_READ_ONLY`、confirm/dry-run、annotations
- **0.2.0** 加密 VM 三入口、超时分档杀进程、vm_resolve/vm_health/vm_log_tail、目录拷贝、结构化错误+hint

工具面现状：**140 个工具**（REST 19 / vmrun 53 / vmcli 65 / core 3）；单元测试 70；黄金快照 141；协议符合性门 9。
