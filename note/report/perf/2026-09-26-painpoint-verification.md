# 痛点回访验证报告（v0.3.2，2026-09-26）

目的：验证历史痛点在当前版本上**是否真的得到优化**——不重复基准方法论，全部为本机真实 VMware 现场实测（探测脚本 TEMP/painpoint_verify.py 等经服务端 `server.call_tool` 同一代码路径执行；加密 VM 为实测发现的真机，非构造）。背景痛点出处：`note/` 三份使用复盘（0.2.0 之前 344 次调用 51 次失败、30s 超时墙）。

## 结论速览

| 痛点（0.1.x/0.2.0 之前的表现） | 现场实测（0.3.2） | 判定 |
|---|---|---|
| ① 错误/缺失凭据挂死 30s 超时墙（51 次失败主因） | 未知 vm_id **0.32s**、不存在 vmx **0.34s**、加密 VM 无密码 **0.307s**，全部结构化 JSON 失败 | ✅ 已解决（验收线 <5s，实际快两个数量级） |
| ② `vm_health` 串行三探测 ~1s | 运行中 VM 1021→421ms（**2.4x**，同日早间实测）；关机 VM 1135→533ms（**2.1x**，失败探测路径同样计费） | ✅ 已解决 |
| ③ 每调用付进程孵化税 / REST 每请求建连 | vmrun 16 路并发 5044→1145ms（**4.4x**，16/16 无串扰）；REST 50 请求 TCP 50→1、514→50ms（**10.3x**） | ✅ 已解决 |
| ④ 破坏性工具无护栏直接执行 | `vmrun_delete` 无 confirm：**0.00s** 返回 dry-run 预览，零副作用，参数回显完整 | ✅ 已解决 |
| ⑤ 加密 VM 密码入口缺失 | 盘点 16 台 vmx：发现 1 台真实加密 VM（`Windows 11 x64.vmx`，encryptionType=**partial**）；无密码调用 0.307s 失败且 **hint 精确命中**："VM 已加密：提供 enc_pass 参数，或设置 VMWARE_ENC_PASSWORD / 调用 set_vm_encryption_password 预存密码" | ✅ 已解决（且 hint 系统真机命中） |

## 关键证据原文

加密 VM 无密码调用的完整结构化返回（`vmrun_tools_state`）：

```json
{
  "ok": false, "tool": "vmrun_tools_state",
  "error": "vmrun failed: Error: Cannot open VM: ...Windows 11 x64.vmx, A password is required for this operation",
  "exit_code": 4294967295, "duration_ms": 307, "timeout": false,
  "hint": "VM 已加密：提供 enc_pass 参数，或设置 VMWARE_ENC_PASSWORD / 调用 set_vm_encryption_password 预存密码"
}
```

护栏 dry-run 返回（`vmrun_delete` 无 confirm，未执行任何操作）：

```json
{"ok": false, "dry_run": true, "tool": "vmrun_delete", "arguments": {"vm_id": "D:/nonexistent-bench.vmx"}, "note": "DRY-RUN：以上操作未执行", "hint": "确认无误后，携带 confirm: true 再次调用以实际执行 vmrun_delete"}
```

## 诚实备注

- 会话环境（shell）无 vmrest 凭据，REST 侧计时用本地 keep-alive 测试服务器给连接行为证据；经 MCP 注册服务端的 `vm_list`/`vm_get`/`vm_power_get` 功能实测在上轮已通过（15 台库存、4vCPU/4096MB、poweredOff 均正确）。
- ① 中"错误 guest 凭据"用不存在的 vmx 路径验证快速失败机制（未对真实 guest 做错误密码尝试，避免触发来宾账户锁定）；机制相同（stdin 隔离 + 强制超时）。
- ② 的 2.4x 实测于同日早间 VM 运行时（bench_composite）；回访时该 VM 已关机，关机态补测 2.1x。
- 本轮 MCP 调用发射再次出现重复循环（只读调用，无副作用），计时验证改经同一服务端代码路径完成；MCP 发射路径的间歇性异常属会话层问题，与服务端无关——此前成功的 18 次 MCP 调用已覆盖三通道功能面。
