# VMware Workstation Pro MCP Server

通过 REST API、vmrun 和 vmcli 控制 VMware Workstation Pro 虚拟机的 MCP 服务器。

## 功能特性

**140 个工具**，覆盖 VMware Workstation Pro 全部自动化能力：

| 来源 | 工具数 | 描述 |
|------|--------|------|
| REST API | 19 | 虚拟机管理、网卡、共享文件夹、端口转发 |
| vmrun | 51 | 电源、快照、克隆、客户机文件/进程/作业操作、目录树传输 |
| vmcli | 65 | 芯片组、磁盘、网卡、SATA、NVMe、串口、VProbes |
| server | 5 | 加密密码管理、VM 解析/体检、日志尾部、截屏 OCR |

按家族口径（`VMWARE_TOOLS` 作用域分类）：REST 19 / vmrun 53 / vmcli 65 / core 3。

## 环境要求

- VMware Workstation Pro 17+
- Python 3.10+
- vmrest 服务运行中（REST API 工具需要）

## 安装

```bash
git clone https://github.com/Guiyuan1111/vmware-mcp-gy.git
cd vmware-mcp
pip install -e .
```

## 配置

启动 vmrest 服务：
```bash
# 首次配置
"C:\Program Files (x86)\VMware\VMware Workstation\vmrest.exe" -C

# 启动服务
"C:\Program Files (x86)\VMware\VMware Workstation\vmrest.exe"
```

添加到 Claude Code：
```bash
claude mcp add vmware-mcp \
  -e VMWARE_USERNAME=your_username \
  -e VMWARE_PASSWORD=your_password \
  -- vmware-mcp
```

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `VMWARE_HOST` | `localhost` | vmrest 地址 |
| `VMWARE_PORT` | `8697` | vmrest 端口 |
| `VMWARE_USERNAME` / `VMWARE_PASSWORD` | 空 | vmrest 凭据 |
| `VMRUN_PATH` / `VMCLI_PATH` | `(x86)` 安装路径 | vmrun/vmcli 可执行文件路径 |
| `VMWARE_ENC_PASSWORD` | 空 | 加密 VM 全局密码（对 vmrun 系工具生效） |
| `VMWARE_TIMEOUT_QUERY` | `30` | 查询类工具超时（秒） |
| `VMWARE_TIMEOUT_POWER` | `90` | 电源类工具超时（秒） |
| `VMWARE_TIMEOUT_LONG` | `600` | clone/upgrade/模板/磁盘等长任务超时（秒） |
| `VMWARE_READ_ONLY` | 关 | 置 `1` 后拒绝全部破坏性工具（护栏第一层） |
| `VMWARE_MAX_CONCURRENCY` | `8` | vmrun/vmcli 子进程并发上限（信号量） |
| `VMWARE_MAX_OUTPUT` | `20000` | 单次工具返回最大字符数，超出截断；`0` 关闭 |
| `VMWARE_TLS_VERIFY` | 关 | 置 `1` 后 REST 连接校验 TLS 证书（vmrest 默认 http，无需开） |
| `VMWARE_LOG_LEVEL` | `WARNING` | stderr 日志级别；`INFO` 起每次调用输出工具名/成败/耗时 |
| `VMWARE_COMPACT_OUTPUT` | 关 | 置 `1` 后成功路径 JSON 紧凑输出（典型负载省 ~25% token）；默认缩进格式 |
| `VMWARE_HOST_TEMP_DIR` | 系统临时目录 | run_job/read_file 宿主侧中转文件目录 |
| `VMWARE_READ_FILE_KB` | `256` | `vmrun_read_file` 单次最大读取 KB，超出置 `truncated: true` |
| `VMWARE_TOOLS` | `all` | 工具面作用域：`rest`/`vmrun`/`vmcli`/`core`（逗号组合）；非法值回退全量。`vm_resolve`/`vm_health`/`vm_log_tail` 为 core，任何作用域保留 |

## 工具面作用域（v0.4.1）

`VMWARE_TOOLS=vmrun` 一类配置把工具列表裁剪到单一家族（域外调用入口即拒），给模型减噪、给暴露面收缩：

| 作用域 | 工具数 | 列表负载（全量 74.8KB） |
| --- | --- | --- |
| 全量（默认） | 140 | 74.8KB |
| `vmrun` | 56 | 35.7KB（48%） |
| `vmcli` | 68 | 31.5KB（42%） |
| `rest` | 22 | 10.6KB（14%） |
| `core` | 3 | 1.5KB（2%） |

> 负载为紧凑 JSON 字节口径；SDK 缺省缩进格式下全量约 104.5KB，各作用域相对比例不变（口径更正见 0.4.2 报告）。

典型 guest 操作工作流建议 `VMWARE_TOOLS=vmrun`；REST 元数据场景 `rest`；两者组合 `vmrun,rest`。协议符合性门：`python benchmark/protocol_conformance.py`（无需 VMware）。

## 工作流组合工具（v0.4.0）

基于 647 次真实会话调用记录的分析（详见 `note/report/perf/2026-09-29-workflow-composite-0.4.0.md`），把高频多连调用合并为单次调用，直接省模型回合：

- **`vmrun_run_job`**：上传脚本文本 → 执行 → 回传 `stdout` + `exit_code`，并自动清理 guest 临时文件。合并 `copy_to + run + copy_from` 三连（3 次调用/3 回合 → 1 次/1 回合）。脚本经子 shell/例程隔离包装（用户 `exit` 不破坏收尾），输出以 `.part` 中转、完成后原子发布。长任务用 `no_wait: true` 启动后配合后两个工具收集。
- **`vmrun_read_file`**：直接读取 guest 文本文件内容（合并 copy_from + 本地 Read）；二进制拒绝并指引 `vmrun_copy_from`；上限 `VMWARE_READ_FILE_KB`。
- **`vmrun_wait_file`**：轮询等待 guest 文件出现（收 `no_wait` 作业产物）；凭据/Tools 类错误首次探测即报，不空耗超时。`timeout_s` 默认 25（ZCode 客户端 30s 掐断，长等待分次调用）。

```json
{"ok": true, "kind": "posix", "job_id": "03d37ddc", "script_guest_path": "/tmp/vmjob-03d37ddc.sh",
 "output_guest_path": "/tmp/vmjob-03d37ddc.out", "exit_code": 0, "stdout": "hello-from-guest\n42"}
```

## 安全护栏

对破坏性操作（删除、断电、还原快照等 23 个工具）实施三层护栏：

1. **全局只读开关**：`VMWARE_READ_ONLY=1` 启动后，破坏性工具直接拒绝（返回 `read_only: true`），查询类工具不受影响；
2. **confirm 二次确认**：未带 `confirm: true` 时，破坏性工具返回 dry-run 预览（含将执行的参数），不产生任何副作用；模型确认后携带 `confirm: true` 重调才实际执行；
3. **annotations 声明**：破坏性工具在工具列表标注 `destructiveHint: true`，只读工具标注 `readOnlyHint: true`，供客户端/模型在调用前甄别。

dry-run 响应示例：

```json
{"ok": false, "dry_run": true, "tool": "vmrun_delete", "arguments": {"vm_id": "D:/vms/a.vmx"}, "note": "DRY-RUN：以上操作未执行", "hint": "确认无误后，携带 confirm: true 再次调用以实际执行 vmrun_delete"}
```

注：`vm_power_set` 仅在 `state: "off"` 时视为破坏性（开机/挂起/暂停不受 confirm 约束）。

## 加密虚拟机

vmrun 底层以 `-vp` 传递加密密码，三种入口（优先级从高到低，可叠加）：

1. 工具参数 `enc_pass`（全部带 `vm_id` 的 `vmrun_*` 工具均支持）；
2. `set_vm_encryption_password` 预存（按 vmx 记忆，存于服务进程内存）；
3. env `VMWARE_ENC_PASSWORD`。

注意：REST/vmrest 与 vmcli 通道对加密 VM 的支持未经验证——**加密 VM 请使用 `vmrun_*` 工具族**。子进程 stdin 已隔离，加密 VM + 无密码时 vmrun 会在数秒内报错返回（不再挂死到客户端 30s 超时）。

## 错误格式与超时

失败调用返回结构化 JSON（成功路径保持原有文本/JSON 不变）：

```json
{"ok": false, "tool": "vmrun_start", "error": "vmrun failed: ...", "exit_code": 1, "stdout": "", "stderr": "...", "duration_ms": 320, "timeout": false, "hint": "VM 已加密：提供 enc_pass 参数，或设置 VMWARE_ENC_PASSWORD"}
```

`hint` 是针对常见错误（要加密密码/密码错误/guest 空密码/Tools 未装/路径不存在）的一句话下一步建议。子进程按工具类别强制超时并杀进程（僵尸 vmrun 不再存活）；输出按 utf-8 → gb18030 解码（中文 Windows 控制台不再乱码）。

### 0.2.0 破坏性变更

- `vmrun_run` / `vmrun_script` 的 `args`：推荐传**字符串数组**（每项一个参数）；传字符串时不再按空格拆分，而是整体作为单个参数透传。原先依赖自动拆分的调用需改为数组。

## 变更历史（按版本倒序；工具数/测试数等为各版本发布时点的史实，当前状态以本文其余章节与 [`note/release/`](note/release/) 为准）

## 0.4.2 变更（通道剖析 + 生命周期剖析）

- `vm_health` REST 快路径：REST vm_id 形态下 `running` 走 power_state 元数据（401 自动回退，字段语义不变）；`VMWARE_TOOLS=rest` 场景下 running 仍可诊断。真机 1.21x。
- 新增基准：`bench_health_channels.py`（通道对照+回退验证）、`bench_lifecycle.py`（冷启动分解）。
- 剖析结论：冷启动 ~900ms 中 mcp SDK import 占 756ms（硬依赖不可避）；`vmrest /ip` 底层同为 VIX 无收益（409 对关机 VM）；性能线封版依据见 [`note/report/perf/2026-09-29-health-rest-fastpath-0.4.2.md`](note/report/perf/2026-09-29-health-rest-fastpath-0.4.2.md)。

## 0.4.1 变更（工具面作用域）

- 新增 `VMWARE_TOOLS`（rest/vmrun/vmcli/core 逗号组合，缺省全量）：按家族裁剪工具列表与调用面，vmrun 作用域 56 工具/48% 负载；core 诊断工具任何作用域保留，域外调用入口即拒。
- 新增 `benchmark/protocol_conformance.py`：stdio JSON-RPC 协议符合性门 9 项（stdout 零污染、dry-run 护栏经协议层在线等，无需 VMware）。

## 0.4.0 变更（工作流组合工具）

- 新增 `vmrun_run_job` / `vmrun_read_file` / `vmrun_wait_file`：把真实会话中最高频的多连调用合并为单次（三连 3 回合→1 回合等），直接返回 stdout+exit_code；见上方「工作流组合工具」节。
- 描述修正：`vmrun_run`/`vmrun_script` 明示不捕获 stdout；`vmrun_copy_dir_to/from` 加「多于 3 个文件优先用我」触发条件。
- 新 env：`VMWARE_HOST_TEMP_DIR`、`VMWARE_READ_FILE_KB`。
- 依据：647 次历史调用记录分析 + RHEL-10 真机三轮迭代验证（原子发布/exit 隔离/符号链接检测）；基准 `benchmark/bench_workflow.py`，报告 [`note/report/perf/2026-09-29-workflow-composite-0.4.0.md`](note/report/perf/2026-09-29-workflow-composite-0.4.0.md)。

## 0.3.2 变更（性能第二期）

- `vm_health` / `vm_resolve` 内部并发化：互不依赖的只读探测并发执行，真实 vmrun 实测 vm_health **2.4x**（1021→421ms）；结果与调用序列经专项单测+黄金快照锁定不变。
- `VMWARE_COMPACT_OUTPUT=1`（可选）：成功路径 JSON 紧凑输出省 ~25% token；默认关闭时输出与 0.3.1 逐字节一致。
- 详见 [`note/report/perf/`](note/report/perf/)。

## 0.3.1 变更（性能优化）

- `tools/list` 工具表进程内缓存：每次枚举 411.7 µs → 0.1 µs（4117x），输出字节级一致。
- `call_tool` 分发链 → 137 项路由表（性能中性、架构收口：圈复杂度 136 → 约 10）；分发行为经黄金快照 138/138 逐字节校验。
- 实测：真实 vmrun 16 路并发 5.1x 加速（16/16 无串扰）；REST 连接复用 50 → 1 TCP 连接（墙钟 11.0x）。基准程序见 [`benchmark/`](benchmark/)，对比报告见 [`note/report/perf/`](note/report/perf/)。

## 0.3.0 变更

- **安全护栏**：`VMWARE_READ_ONLY` 全局只读开关、破坏性工具 confirm/dry-run 二次确认、工具列表 annotations（只读/破坏性提示）；工具面零破坏——`confirm` 为可选新增参数，不传时行为变为 dry-run（严格说这是安全语义修正，见 release note）。
- **性能**：REST 走进程级 httpx 连接池（复用 TCP 连接）；vmrun/vmcli 子进程并发上限（`VMWARE_MAX_CONCURRENCY`，默认 8）；超长输出自动截断（`VMWARE_MAX_OUTPUT`，默认 20000 字符）。
- **代码卫生**：删除 6 个无引用死方法；guest 密码与加密密码在错误输出/日志中脱敏（argv 明文传递是 vmrun/vmcli 机制本身，无法根治，见加密节警告）；`VMWARE_TLS_VERIFY` 可配。
- **可观测性**：每次调用向 stderr 记一行工具名/成败/耗时（`VMWARE_LOG_LEVEL=INFO` 开启，默认静默）。
- **集成验证**：41 项单元测试 + 只读集成实测通过。

## 测试

```bash
pip install -e ".[dev]"
pytest                              # 单元测试（无需 VMware）
VMWARE_IT=1 pytest -m integration   # 集成测试（需真实 VMware 环境）
python benchmark/protocol_conformance.py  # stdio 协议符合性门 9 项（无需 VMware）
```

性能基准（`benchmark/`，详见 [`note/report/perf/`](note/report/perf/)）：

```bash
python benchmark/bench_list_tools.py               # tools/list 构建 vs 缓存
python benchmark/bench_dispatch.py                 # 分发机制（+端到端开销）
python benchmark/bench_subprocess.py               # 真实 vmrun 串行 vs 信号量并发 + 隔离校验
python benchmark/bench_rest.py                     # REST 每请求连接 vs 共享池
python benchmark/bench_composite.py                # vm_health/vm_resolve 内部并发（0.3.2）
python benchmark/bench_output.py                   # 紧凑输出负载对比（0.3.2）
python benchmark/bench_hotpath.py                  # 每调用热路径函数基准（0.3.1，含被否决候选记录）
python benchmark/bench_workflow.py                 # 工作流组合工具 vs 多连调用（0.4.0，需 VM）
python benchmark/bench_health_channels.py          # vm_health 通道对照 + 回退验证（0.4.2，需凭据）
python benchmark/bench_lifecycle.py                # 冷启动/生命周期剖析（0.4.2）
python benchmark/verify_dispatch_equivalence.py check  # 分发行为黄金快照回归门（141 项）
```

## 工具列表

### REST API 工具
| 工具 | 描述 |
|------|------|
| `vm_list` | 列出所有虚拟机 |
| `vm_get` | 获取虚拟机设置 |
| `vm_create` | 克隆虚拟机 |
| `vm_delete` | 删除虚拟机 |
| `vm_update` | 更新虚拟机 CPU/内存 |
| `vm_power_get` | 获取电源状态 |
| `vm_power_set` | 设置电源状态（on/off/shutdown/suspend/pause/unpause） |
| `vm_nic_list` | 列出网络适配器 |
| `vm_nic_create` | 创建网络适配器 |
| `vm_nic_delete` | 删除网络适配器 |
| `vm_ip_get` | 获取虚拟机 IP 地址 |
| `vm_folder_list` | 列出共享文件夹 |
| `vm_folder_create` | 创建共享文件夹 |
| `vm_folder_delete` | 删除共享文件夹 |
| `network_list` | 列出虚拟网络 |
| `network_create` | 创建虚拟网络 |
| `network_portforward_list` | 列出端口转发 |
| `network_portforward_set` | 设置端口转发 |
| `network_portforward_delete` | 删除端口转发 |

### vmrun 工具
| 工具 | 描述 |
|------|------|
| `vmrun_list` | 列出运行中的虚拟机 |
| `vmrun_start` | 启动虚拟机 |
| `vmrun_stop` | 停止虚拟机（软/硬） |
| `vmrun_reset` | 重置虚拟机 |
| `vmrun_suspend` | 挂起虚拟机 |
| `vmrun_pause` | 暂停虚拟机 |
| `vmrun_unpause` | 恢复暂停的虚拟机 |
| `vmrun_clone` | 克隆虚拟机（完整/链接） |
| `vmrun_upgrade` | 升级虚拟机格式 |
| `vmrun_delete` | 删除虚拟机 |
| `vmrun_snapshot_list` | 列出快照 |
| `vmrun_snapshot_take` | 创建快照 |
| `vmrun_snapshot_delete` | 删除快照 |
| `vmrun_snapshot_revert` | 恢复快照 |
| `vmrun_file_exists` | 检查客户机文件是否存在 |
| `vmrun_dir_exists` | 检查目录是否存在 |
| `vmrun_ls` | 列出客户机目录 |
| `vmrun_mkdir` | 在客户机创建目录 |
| `vmrun_rmdir` | 删除客户机目录 |
| `vmrun_rm` | 删除客户机文件 |
| `vmrun_rename` | 重命名客户机文件 |
| `vmrun_copy_to` | 复制文件到客户机 |
| `vmrun_copy_from` | 从客户机复制文件 |
| `vmrun_temp_file` | 在客户机创建临时文件 |
| `vmrun_copy_dir_to` | 递归复制目录树到客户机（include/exclude 后缀过滤） |
| `vmrun_copy_dir_from` | 递归复制客户机目录树到宿主机 |
| `vmrun_run` | 在客户机运行程序（不捕获程序 stdout） |
| `vmrun_script` | 在客户机运行脚本（script 为 guest 内文件路径；不捕获 stdout） |
| `vmrun_run_job` | 一键作业：上传脚本文本→执行→回传 stdout+exit_code 并清理临时文件（合并三连调用） |
| `vmrun_read_file` | 直读 guest 文本文件内容到对话 |
| `vmrun_wait_file` | 轮询等待 guest 文件出现（收后台作业产物） |
| `vmrun_ps` | 列出客户机进程 |
| `vmrun_kill` | 终止客户机进程 |
| `vmrun_shared_enable` | 启用共享文件夹 |
| `vmrun_shared_disable` | 禁用共享文件夹 |
| `vmrun_shared_add` | 添加共享文件夹 |
| `vmrun_shared_remove` | 移除共享文件夹 |
| `vmrun_shared_set` | 设置共享文件夹状态 |
| `vmrun_device_connect` | 连接设备 |
| `vmrun_device_disconnect` | 断开设备 |
| `vmrun_var_read` | 读取虚拟机变量 |
| `vmrun_var_write` | 写入虚拟机变量 |
| `vmrun_screenshot` | 截取屏幕 |
| `vmrun_keystrokes` | 发送按键 |
| `vmrun_tools_install` | 安装 VMware Tools |
| `vmrun_tools_state` | 检查 Tools 状态 |
| `vmrun_guest_ip` | 获取客户机 IP |
| `vmrun_host_networks` | 列出主机网络 |
| `vmrun_portforward_list` | 列出端口转发 |
| `vmrun_portforward_set` | 设置端口转发 |
| `vmrun_portforward_delete` | 删除端口转发 |

### vmcli 工具
| 工具 | 描述 |
|------|------|
| `snapshot_list` | 列出快照 |
| `snapshot_take` | 创建快照 |
| `snapshot_revert` | 恢复快照 |
| `snapshot_delete` | 删除快照 |
| `snapshot_clone` | 从快照克隆 |
| `guest_run` | 运行程序 |
| `guest_ps` | 列出进程 |
| `guest_kill` | 终止进程 |
| `guest_ls` | 列出文件 |
| `guest_mkdir` | 创建目录 |
| `guest_rm` | 删除文件 |
| `guest_rmdir` | 删除目录 |
| `guest_copy_to` | 复制到客户机 |
| `guest_copy_from` | 从客户机复制 |
| `guest_env` | 获取环境变量 |
| `mks_screenshot` | 截取屏幕 |
| `mks_send_key` | 发送按键序列 |
| `mks_query` | 查询 MKS 状态 |
| `chipset_query` | 查询 CPU/内存配置 |
| `chipset_set_cpu` | 设置 CPU 数量 |
| `chipset_set_memory` | 设置内存大小 |
| `chipset_set_cores` | 设置每插槽核心数 |
| `tools_query` | 查询 Tools 状态 |
| `tools_install` | 安装 Tools |
| `tools_upgrade` | 升级 Tools |
| `template_create` | 创建虚拟机模板 |
| `template_deploy` | 部署虚拟机模板 |
| `disk_query` | 查询磁盘配置 |
| `disk_create` | 创建磁盘 |
| `disk_extend` | 扩展磁盘 |
| `config_query` | 查询配置参数 |
| `config_set` | 设置配置参数 |
| `power_query` | 查询电源状态 |
| `power_start` | 启动虚拟机 |
| `power_stop` | 停止虚拟机 |
| `power_pause` | 暂停虚拟机 |
| `power_unpause` | 恢复虚拟机 |
| `power_reset` | 重置虚拟机 |
| `power_suspend` | 挂起虚拟机 |
| `ethernet_query` | 查询网卡配置 |
| `ethernet_set_type` | 设置连接类型 |
| `ethernet_set_present` | 设置适配器存在 |
| `ethernet_set_connected` | 设置启动时连接 |
| `ethernet_set_device` | 设置虚拟设备 |
| `ethernet_set_network` | 设置网络名称 |
| `ethernet_purge` | 移除适配器 |
| `hgfs_query` | 查询共享文件夹 |
| `hgfs_set_enabled` | 启用/禁用共享 |
| `hgfs_set_path` | 设置主机路径 |
| `hgfs_set_name` | 设置客户机名称 |
| `hgfs_set_read` | 设置读取权限 |
| `hgfs_set_write` | 设置写入权限 |
| `serial_query` | 查询串口 |
| `serial_set_present` | 设置串口存在 |
| `serial_purge` | 移除串口 |
| `sata_query` | 查询 SATA 配置 |
| `sata_set_present` | 设置 SATA 存在 |
| `sata_purge` | 移除 SATA 适配器 |
| `nvme_query` | 查询 NVMe 配置 |
| `nvme_set_present` | 设置 NVMe 存在 |
| `nvme_purge` | 移除 NVMe 适配器 |
| `vprobes_query` | 查询 VProbes |
| `vprobes_enable` | 启用 VProbes |
| `vprobes_load` | 加载 VProbes 脚本 |
| `vprobes_reset` | 重置 VProbes |

### server 工具
| 工具 | 描述 |
|------|------|
| `set_vm_encryption_password` | 预存加密 VM 密码（进程内存，按 vmx 记忆） |
| `vm_resolve` | 模糊名/路径片段解析 VM（vmx 路径 + 电源 + 加密类型） |
| `vm_health` | 一次返回电源/Tools/IP/加密/空闲挂起判据/log 尾部 |
| `vm_log_tail` | 读 vmware.log 尾部 N 行 |
| `screenshot_ocr` | VM 截屏并 OCR 成文本（需 `pip install ".[ocr]"`） |

## 许可证

MIT
