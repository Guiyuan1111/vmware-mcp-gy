# vmware-mcp-gy 性能优化对比报告（v0.3.1，2026-09-26）

目标：在不影响安全性、稳定性、兼容性（红线）的前提下优化 MCP 性能。本报告量化 0.3.0 → 0.3.1 的优化前后对比。全部基准程序位于 [`benchmark/`](../../../../benchmark/)，可一键复现。

## 方法

- 每项优化配独立基准（`bench_*.py`），输出中位数/P95 与机器可读 JSON 行；
- **行为等价红线**：结构性重写用黄金快照校验——重构前录制全部 138 个分发入口（137 工具 + 未知工具名）的「方法调用序列 + 返回文本」，重构后逐字节比对（`verify_dispatch_equivalence.py`，快照 `goldens_dispatch.json`）；
- 候选优化一律先基准后采纳：两项候选被实测否决并回滚（见 §3），避免"感觉快"式改动；
- 环境：Windows 10.0.26300 x64，Python 3.14，VMware Workstation（vmrun 实测，vmrest 需凭据故 REST 用本地 keep-alive 测试服务器给连接行为证据）。

## 结果总览（终版数据）

| # | 优化点 | 优化前 (0.3.0) | 优化后 (0.3.1) | 提升 | 验证 |
|---|--------|---------------|---------------|------|------|
| 1 | `tools/list` 工具表构建（每次连接/枚举都发生） | 411.7 µs/次（P95 633.7） | 0.1 µs/次（缓存命中） | **4117x** | 序列化输出字节级一致；payload 84.9 KB 不变 |
| 2 | 子进程并发（真实 vmrun ×16，信号量上限 8） | 串行 5184 ms | 并发 1023 ms | **5.1x** | 隔离校验 16/16 无串扰 |
| 3 | REST 连接复用（50 请求） | 50 个 TCP 连接，607 ms | 1 个连接（keep-alive），55 ms | **11.0x 墙钟，连接数 50→1** | 本地 HTTP/1.1 测试服务器实测端口数 |
| 4 | call_tool 分发链 → 路由表 | 链扫描 1.75 µs | 查表 3.61 µs | **性能中性**（见 §3） | 138/138 快照字节等价 |
| 5 | 热路径候选（vmx 正则定位/ASCII 解码快径） | shipped 117-128 µs / 0.3-2.3 µs | attempt 均更慢 | **否决回滚**（见 §3） | 等价断言 + 基准落档 |

服务端单次调用 Python 侧总开销（护栏+分发+提取+序列化+截断，排除 I/O 等待）：**中位 9.8 µs，P95 11.2 µs**——与子进程/REST 的百 ms 级等待相比，Python 侧已可忽略。

## 如实记录：负结果与性能中性的改动

1. **路由表化（#4）性能中性，属架构收益**：CPython 下 135 次失败的 `==` 比较（1.75 µs）不慢于 dict 查表+参数提取器闭包（3.61 µs）；分发从来不是瓶颈。保留该重写是因为它收口了代码分析报告的"发现 3"（圈复杂度 136 → 约 10，137 条路由逐条可测），且对性能无损害。
2. **`_vmx_encryption` 整读+正则定位：否决（0.85-0.98x）**。文件 I/O ~100 µs 主导，`encryptionType` 通常在前几十行、逐行扫描提前命中即停占优。
3. **`decode_output` ASCII 快速路径：否决（0.55-1.0x）**。utf-8 解码对 ASCII 本就近 memcpy 速度，isascii 双趟扫描反而多一次遍历。
4. **`redact_secrets` 正则化：未实验即否决**。正则 alternation 与顺序 replace 在级联替换边界语义不同（红线），且仅在失败路径执行、`str.replace` 是 C 速度。
5. **`_truncate_output` 不动**：O(1) len 比较；env 读取是既有运行时可变契约（测试依赖）。

## 安全 / 稳定 / 兼容验证（红线门）

| 门 | 结果 |
|----|------|
| 单元测试 | 47/47 通过（45 既有 + 2 项行为锁新增） |
| 分发等价性黄金快照 | 138/138 条目逐字节一致（含错误路径与调用序列） |
| `tools/list` 输出 | 序列化字节级一致 |
| 并发数据隔离 | 16/16（8 成功路输出纯净 + 8 报错路只含各自 vmx 路径） |
| 工具面 | 137 个工具，名称/参数/描述零变化；护栏（READ_ONLY/confirm/annotations）行为不变 |

## 复现

```bash
python benchmark/bench_list_tools.py          # tools/list 构建 vs 缓存
python benchmark/bench_dispatch.py            # 分发链模型 vs 路由表（+端到端）
python benchmark/bench_hotpath.py             # 热路径候选评估记录（含否决证据）
python benchmark/bench_subprocess.py          # 真实 vmrun 串行 vs 信号量并发 + 隔离
python benchmark/bench_rest.py                # REST 每请求连接 vs 共享池
python benchmark/verify_dispatch_equivalence.py check   # 分发行为回归门
python -m pytest                              # 单元测试
```
