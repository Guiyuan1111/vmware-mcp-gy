# 调研：AI / LLM Agent 调用 VMware（vSphere / vCenter / Workstation）自动化管理

> 调研日期：2026-09-26。范围：2023–2026 年的研究论文、开源项目与官方方案。
> 方法：4 个并行检索子代理（其中 1 个因搜索服务限流失败，另以精简补查完成）。
> **验证标注**：✅ = 子代理实际抓取页面原文核实（引文来自抓取内容，非记忆）；⚠️ = 仅来自搜索结果摘要，未抓取原文，以链接原文为准。

## 一、总览结论

1. **MCP × VMware 是 2025–2026 年社区爆发的方向**，但绝大多数项目没有只读/安全护栏；README 层面唯一实现"dry-run + confirm + 全局只读开关"三重护栏的是 [TheEvalon/vmware-vcenter-mcp](https://github.com/TheEvalon/vmware-vcenter-mcp)。
2. **arXiv 上不存在** 2023–2026 年以"自然语言→PowerCLI/govc/vSphere API"为主题的论文（arXiv 站内检索 `PowerCLI`=0、`LLM vSphere automation`=0、`natural language VMware automation`=0）。该方向的技术方案几乎全部以 GitHub MCP server 形式存在；学术工作集中在泛 IaC 生成与 AIOps。
3. **Broadcom 官方**：AI Assistant for VCF（VCF 9.1.1 引入，2026-09）支持自然语言运维私有云，但官方原文的能力边界为**观察/诊断/建议/生成管理包**，无执行变更表述；官方 MCP 支持仅列为"未来项目"；GitHub 上 `vmware`/`broadcom` 组织下无官方 MCP server。
4. 行业共识的只读护栏是三层组合：vCenter 原生 Read-Only 角色（硬护栏）→ MCP server 自建只读开关 → 客户端人工确认/沙箱。

## 二、MCP 服务器 × VMware（开源项目）

| 项目 | 链接 | 核心能力 | 工具数 | 适用场景 | 只读/安全护栏 | 验证 |
|---|---|---|---|---|---|---|
| TheEvalon/vmware-vcenter-mcp | https://github.com/TheEvalon/vmware-vcenter-mcp | vCenter 8.0 全生命周期（清单/统计/快照/电源/vMotion/ISO/DRS/HA/vLCM），Automation REST + VI/JSON + SOAP 三 API 面兜底 | README 明写 "100+" | vSphere/vCenter 8.0 | **三层护栏**（README 原文）：破坏性工具须 `confirm: true` 否则返回结构化 dry-run；全局 `VCENTER_READ_ONLY=true` 一票否决；只读集成测试接入发布流程 | ✅ |
| ZacharyZcR/vmware-mcp | https://github.com/ZacharyZcR/vmware-mcp | Workstation 三通道（REST+vmrun+vmcli）；**本仓库的上游** | README 写 117（已过时；本仓库实测 130，0.2.0 后为 137） | 仅 Workstation Pro | 无 | ✅（上游 README；130 为本地实测） |
| nholuongut/VMWare-MCP-server-for-LLMs | https://github.com/nholuongut/VMWare-MCP-server-for-LLMs | govc 封装路线代表：VM 生命周期/快照/数据存储/网络 + `govc_run` 逃生舱；TOON 格式省 token | 55 + 3 元工具 | vSphere | 无 Security 章节；仅 `GOVC_INSECURE`（关 TLS 校验）；许可非标准开源 | ✅ |
| Korotkov113/vcenter-mcp | https://github.com/Korotkov113/vcenter-mcp | vCenter 8/ESXi 8：VM/主机/集群/网络（含 `diagnose_network` 诊断）/数据存储/指标，401 自动重认证 | 未标明（逐表清点约 59，清点值非原文） | vSphere | 无；仅 `VCENTER_SSL_VERIFY` 环境变量 | ✅ |
| VMware-AIops（vmware-skills 组织） | https://github.com/vmware-skills/VMware-AIops | AI 驱动 vCenter/ESXi 生命周期+部署；配套只读监控/存储/Tanzu 等 skill 家族，分诊→调查→执行 | 60 | vSphere 8/Tanzu | 家族内有只读监控 skill；本体未见护栏描述 | ✅ |
| giuliolibrando/vmware-vsphere-mcp-server | https://github.com/giuliolibrando/vmware-vsphere-mcp-server | Docker 化，经 vCenter REST API 暴露电源/快照/模板/资源调整 | 未标明 | vCenter | README 称破坏性操作需显式确认 + 审计日志 | ✅ |
| Zettagrid/zettagrid-vmware-mcp | https://github.com/Zettagrid/zettagrid-vmware-mcp（博文：https://blog.zettagrid.com/zettagrid-vmware-mcp-server-now-available-on-github ） | VMware Cloud Director API 的 MCP（OAuth/多 Zone） | 未标明 | Cloud Director | **反例**：安全节仅凭据/HTTPS/审计，无只读模式 | ✅ |
| 同路线衍生（未逐一核 README） | omichelbraga/vmware-mcp（自称 136 工具，Workstation 26）；danielxxomg/vmware-workstation（自称 confirmation-gated 的 Agent Skill）；vikramjeet8105-engg/vm-lab-agent（自称沙箱+白名单+确认+审计）；mgovedarov/mcp-vcf-orchestrator（VCF Orchestrator 工作流）——GitHub 搜索页可见：https://github.com/search?q=vmware+workstation+mcp&type=repositories | — | — | Workstation/vSphere/VCF | 均以搜索页描述为限 | ⚠️ |

## 三、学术论文（泛基础设施方向，均非 VMware 专用）

| 论文 | 链接 | 机构/年份 | 核心能力 | 与 VMware 关系 | 验证 |
|---|---|---|---|---|---|
| TerraFormer | https://arxiv.org/abs/2601.08734 | ICSE 2026 | 验证器引导 RL 微调生成/变异 Terraform IaC，正确率 +12–20% | 无 | ✅ |
| LLM and Infrastructure as a Code use case | https://arxiv.org/abs/2309.01456 | ENS Rennes, 2023 | LLM 生成 Ansible roles/playbooks 早期探索 | 无 | ✅ |
| AIOpsLab | https://arxiv.org/abs/2501.06706 | Microsoft 背景, 2025 | 自治云运维 agent 评测框架（故障注入/负载/遥测） | 无 | ✅ |
| Building AI Agents for Autonomous Clouds | https://arxiv.org/abs/2407.12165 | Microsoft 背景, 2024 | 自治云 agent 设计原则愿景论文 | 无 | ✅ |
| InfraMind | https://arxiv.org/abs/2509.13704 | NTU, 2025 | 数据中心管理软件 GUI agent：探索式操作 + **VM 快照回滚** + 多层安全机制 | 相邻（DCIM） | ✅ |
| Chat-Driven Optimal Management for Virtual Network Services | https://arxiv.org/abs/2512.24614 | 2025 | NL 意图抽取 + 整数规划 → VM 放置与路由配置 | 无 | ✅ |
| A Survey of AIOps in the Era of LLMs | https://arxiv.org/abs/2507.12472 | 北大等, 2025 | 183 篇 LLM4AIOps 系统综述（CSUR 接收） | 无 | ✅ |
| IaC 安全簇：IaC-Guard-V / TerraRepair / TerraProbe / Compared to What? | https://arxiv.org/abs/2609.28488 / https://arxiv.org/abs/2607.11390 / https://arxiv.org/abs/2606.26590 / https://arxiv.org/abs/2608.28021 | 2025–2026 | IaC 修复验证；**LLM 生成 IaC 漏洞密度为人类基线 3.21–3.87 倍**（Compared to What?） | 无 | ✅（arXiv 元数据核实，摘要未逐一全文阅读） |
| 数据中心相邻簇 | https://arxiv.org/abs/2608.18503（GPU 调度）、https://arxiv.org/abs/2511.00116（液冷 LC-Opt）、https://arxiv.org/abs/2505.19409（数字孪生）、https://arxiv.org/abs/2601.22633（MCP-Diag） | 2025–2026 | LLM 预测调度/RL 基准/孪生构建/网络诊断 | 无 | ✅（元数据核实） |

## 四、安全权限 / 确认机制 / 只读护栏

| 方案 | 链接 | 类型 | 核心内容 | 验证 |
|---|---|---|---|---|
| vCenter Read-Only / No Access 角色 | https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere/8-0/vsphere-security/vsphere-permissions-and-user-management-tasks/using-roles-to-assign-privileges.html | 平台硬护栏 | Read Only 仅可查看对象状态，原文 "All actions through the menus and toolbars are disallowed"——AI 服务账号只读护栏的原生机制 | ✅ |
| 角色最小权限最佳实践 | https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere/8-0/vsphere-security/vsphere-permissions-and-user-management-tasks/best-practices-for-roles-and-permissions.html | 平台 | 最小对象范围 + 传播控制 + No Access 屏蔽清单区域 | ✅ |
| MCP 规范 Security Best Practices | https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices | 协议规范 | Scope Minimization：初始只读最小 scope，高权限操作渐进提权，禁止通配 scope | ✅ |
| GitHub 官方 MCP Server | https://github.com/github/github-mcp-server | 标杆实现 | `--read-only` 只读**优先于**显式工具选择；`--toolsets` 分组；`--lockdown-mode` 防注入（Docker 默认启用） | ✅ |
| Claude Code 安全文档 | https://code.claude.com/docs/en/security | 客户端确认 | Manual mode **默认只读启动**、逐次批准；独立分类器拦截；未匹配命令 fail-closed；沙箱 bash | ✅ |
| OWASP LLM06:2025 Excessive Agency | https://genai.owasp.org/llmrisk/llm062025-excessive-agency/（总览：https://genai.owasp.org/llm-top-10/ ） | 行业标准 | 过度功能/权限/自主三根因；缓解 = 只读 scope + 高影响操作人工批准 + 完全仲裁 | ✅ |
| Invariant Labs：Tool Poisoning Attacks | https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks | 安全研究（2025-04） | 恶意指令藏进工具描述（模型可见用户不可见）；警示**只读 ≠ 安全** | ✅ |
| InjecAgent | https://arxiv.org/abs/2403.02691 | ACL 2024 Findings | 1054 用例：工具型 agent 被间接注入普遍中招；ReAct GPT-4 攻击成功率 24% | ✅ |
| AgentDojo | https://arxiv.org/abs/2406.13352 | ETH Zurich | 97 任务 + 629 攻防用例的护栏评测方法学（代码：https://github.com/ethz-spylab/agentdojo ） | ✅ |
| Anthropic 注入防御 / MCP 代码执行 | https://www.anthropic.com/research/prompt-injection-defenses / https://www.anthropic.com/engineering/code-execution-with-mcp | 官方研究/工程 | Best-of-N 攻击下 ASR 降至约 1%（官方称"远未解决"）；不可信数据不进模型上下文 | ✅ |
| ToolSword / SafeToolBench / AgentGuard | https://arxiv.org/abs/2402.10753 / https://arxiv.org/abs/2509.07315 / https://arxiv.org/abs/2502.09809 | arXiv | 工具调用安全评测谱系（仅元数据核实） | ⚠️ |

## 五、Broadcom / VMware 官方 AI

| 产品/公告 | 链接 | 时间 | 核心能力 | 状态 | 只读护栏 | 验证 |
|---|---|---|---|---|---|---|
| AI Assistant for VCF | https://blogs.vmware.com/cloud-foundation/2026/09/03/new-ai-and-kubernetes-private-cloud-operations-capabilities-in-vmware-cloud-foundation-9-1-1/ ；配置文：https://williamlam.com/2026/09/vcf-9-1-1-enabling-the-the-new-ai-assistant-for-vcf-in-vcf-operations.html | 2026-09-03 | 原文 "operate private cloud environments using simple English queries"；vSphere+VCF 管理服务诊断可见性、告警/配置/日志关联；本地配置自选 LLM（PAIS / 私有 Gemini），保数据主权。VCF Operations 中以扩展 "VMware Intelligent Assist" 启用 | 随 VCF 9.1.1 GA | 原文仅诊断/建议/生成管理包，**无执行变更表述** | ✅ |
| Intelligent Assist with VCF（预告） | https://blogs.vmware.com/cloud-foundation/2025/08/26/vmware-private-ai-foundation-with-nvidia-explore/ | 2025-08-26（Explore 2025） | LLM 助手接入 VCF 访问 Broadcom 知识库；**同页把 MCP 支持列为未来项目**（附"不承诺交付"免责声明） | tech preview（⚠️ 仅搜索摘要） | 未提及变更执行 | ✅（预告页） |
| Broadcom "AI 原生平台" 新闻稿 | https://www.broadcom.com （新闻室 JS 渲染，正文不可抓） | 2025-08-26 | 摘要原文："Intelligent Assist for VCF: An AI-driven support assistant (currently in tech preview)" | tech preview | 未提及变更执行 | ⚠️ |
| VMware Private AI Foundation with NVIDIA | https://blogs.vmware.com/cloud-foundation/2024/05/07/whats-new-in-the-vmware-private-ai-foundation-with-nvidia-ga-release/ | 2024-05-07 | 在 vSphere 上跑 LLM/RAG（NIM/NeRetriever）——是"vSphere 承载 AI"而非"AI 管 vSphere" | GA | 不适用 | ✅ |
| VCF Private AI Services | https://blogs.vmware.com/cloud-foundation/2025/06/19/private-ai-services-new-in-vmware-private-ai-foundation-with-nvidia-in-vcf-9-0/ | 2025-06-19 | Model Store / 推理端点（vLLM 等）/ 检索 / Agent Builder，随 VCF 9.0 | 随 9.0 | 不适用 | ✅ |
| Summarize-and-Chat | https://blogs.vmware.com/cloud-foundation/2024/08/28/summarize-and-chat-service-vmware-private-ai/ | 2024-08-28 | 开源摘要/问答服务 | 开源 | 只读（文档摘要问答） | ✅ |
| "VCF Observer" | —（官方域名下不存在；检索命中均为基因学工具，如 https://github.com/MBaysanLab/vcf-observer ） | — | **否定性结论：Broadcom/VMware 无此产品**。相近者为 VCF 5.2 Diagnostics Console 与 VCF Diagnostic Tool（https://knowledge.broadcom.com/external/article/344917/using-the-vcf-diagnostic-tool-for-vspher.html ，⚠️） | 不存在 | — | ✅（否定性验证） |
| 2023 Intelligent Assist（Explore 2023） | 仅第三方：https://www.wwt.com/blog/vmware-explore-2023-key-takeaways | 2023-08 | WWT 转述：面向 Tanzu/NSX+/Workspace ONE 的 GenAI 套件，基于 Private AI | 官方原文不可达（news.vmware.com 301 至 JS 新闻室；web.archive.org 超时） | 未提及 | ⚠️（仅第三方转述） |

## 六、检索方法、限制与诚实声明

- **有效搜索词**：`VMware MCP server github`；GitHub API `vsphere mcp`（27 结果）/`esxi mcp ai`/`govc llm`/`vmware assistant`；`arXiv "infrastructure as code large language model"`、`arXiv "AIOps large language model agent operations"`；`"VCF AI Assistant" Broadcom`、`Intelligent Assist VCF tech preview`；`github-mcp-server read-only flag`；`arXiv LLM agent tool use safety benchmark AgentDojo InjecAgent`；`Invariant Labs MCP tool poisoning`。
- **无结果的词（如实报告）**：arXiv 站内 `LLM vSphere automation`、`natural language VMware automation`、`PowerCLI` 均 0 结果；GitHub `powercli llm`=0、`topic:vmware-mcp`=0；Broadcom/VMware 官方组织下无 MCP server 仓库；"VCF Observer" 官方命中为空。
- **环境限制**：检索期间搜索/抓取服务多次 429 限流与超时；`news.vmware.com` 旧新闻稿 301 至 JS 渲染页无法取正文；web.archive.org 全部连接超时；中文检索词因限流未执行成功。上述限制导致：2023 年 Aria/Intelligent Assist 一节仅有第三方转述（表五末行）；少量项目只有搜索摘要（各表 ⚠️ 行）。
- 本文中所有 ✅ 条目的引文均来自子代理实际抓取的页面原文，非记忆生成；⚠️ 条目请以链接原文为准。

## 七、对本项目（vmware-mcp-gy）的启示

1. **护栏是社区普遍缺口、可差异化点**：调研到的 Workstation/vSphere MCP 项目中，仅 TheEvalon 实现了完整护栏。本项目 0.2.0 已有结构化错误 + 快速失败 + 加密密码入口，下一步可借鉴其三层设计：破坏性工具 `confirm` 参数 → dry-run 返回 → `VMWARE_READ_ONLY` 全局开关 + 只读集成测试（对应 Blueprint 04 的防漂移/一致性思路）。
2. **Workstation 赛道玩家少**：vSphere/vCenter 方向项目密集，Workstation 方向（本仓库定位）主要是 ZacharyZcR/vmware-mcp 及少量衍生，README 级护栏均为空白。
3. **官方路线参考**：Broadcom 官方助手（Intelligent Assist）能力边界是"诊断/建议"而非执行变更，与本调研"只读优先"的护栏共识一致；其 MCP 支持仍是未来项目，社区 server 是当前唯一集成路径。
4. **工具描述即安全面**：Tool Poisoning 研究表明工具描述可被注入——本项目 0.2.0 的描述重写虽提升可选择性，也意味着描述变更需走审查流程（与 Blueprint 04 审计呼应）。
