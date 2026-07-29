# SocialEase Agent Harness 设计

SocialEase 采用轻量的 **Model + Harness** 架构。LLM 只负责语义理解、生成或抽取的可选增强；真正的安全边界、权限判断、同意机制、记忆写入和 trace 都由 harness 控制。

```text
Agent = Model + Harness

Harness = Skills + Knowledge + Observation + Action Interfaces + Permissions
```

## 设计目标

- 不把系统做成单轮心理聊天机器人；
- 在社交压力场景中保持非医疗化边界；
- crisis 输入绕过普通 agent，进入 escalation flow；
- 所有主动练习 action 都可被 permission gate 和 consent protocol 管住；
- 长期记忆只注入低敏结构化上下文；所有多轮模式共用从 Timeline 构建、带 Token Budget
  的 Working Context，Redis 只缓存可重建投影；
- trace、metrics、eval gate 让系统行为可解释、可回归。

## 当前架构快照

当前 SocialEase 是一个产品化 Agent 原型，核心链路已经可运行：

- `/chat` 是唯一主对话界面，写入统一的 Conversation Timeline；
- `/api/conversations/{conversation_id}/messages` 是当前产品消息入口；旧 `/api/chat`
  和 `/api/chat/stream` 只保留为已标记 deprecated 的迁移兼容接口；
- Conversation Service 在 Harness 之外负责 owner scope、事件顺序、幂等键、有界上下文、
  模块 Proposal 和模块栈生命周期；
- Safety classification 在 routing 和 skill execution 前执行；
- Intent routing 可继续普通 support，也可生成 role-play、worksheet、exposure 或
  resource 的严格校验 Proposal；模型不能接受 Proposal 或启动、结束模块；
- 用户确认后，模块通过独立 Adapter 复用领域 Service；允许的父子组合可以嵌套，
  最大深度三层，当前顶层模块接收下一条消息；
- 主动练习通过 `SafetyPermissionGate` 和 consent protocol；
- hooks 提供 metrics、privacy guard 和未来审计扩展点；
- memory export/delete、practice preference consent 已实现；
- LLM provider 支持 retry、circuit breaker、timeout、deterministic fallback；
- protocol 支持 request hash、session binding、过期、同意/拒绝、一次性消费、replay resistance；
- intervention plan 可视化为 timeline，记录当前步骤、进度、protocol 绑定、stop condition 和结果摘要；
- auth 同时支持本地演示模式和 production bearer-token/cookie 模式；
- PostgreSQL 是唯一持久化运行时，领域层通过 Repository Protocol 与 adapter 解耦。
- 完整 Conversation History、提供给模型的 Working Context 和长期 Agent Memory
  是三个不同生命周期的数据面；
- Conversation 正文在 production 使用 AES-256-GCM；缺少内容密钥时持久化层拒绝启动；
- 统一 Conversation Context、Module Overlay、Worksheet Draft 和 Resource Citation
  指代使用加密或最小化的类型化 Redis 状态；production 默认要求 Redis；
- Calendar Planning Skill 只生成受限提醒提案，外部写操作通过 owner-bound Consent、幂等键和 MCP Tool Contract 执行；
- 所有 Skill 输出在写 Memory、记录 Trace 和返回 API 前统一经过 Output Guardrail，并支持一次 Repair 与二次复检。

验证不在设计文档中固化易过期的测试数字。当前可复现基线以
[`benchmark_report.md`](benchmark_report.md) 和 CI 为准，主要门禁包括：

```text
backend pytest + PostgreSQL/Redis integration
deterministic eval + product-boundary gate
frontend typecheck + lint + build
demo auth / production auth E2E + real-stack smoke
```

## 统一会话 Runtime Loop

```text
User Input
  -> ConversationService: owner scope + idempotency + append user event
  -> build bounded Working Context from timeline + compact summary
  -> SafetyClassifier
  -> crisis: preempt module stack and append escalation result
  -> active module: dispatch to top ModuleAdapter
  -> ordinary conversation: AgentHarness
  -> before_safety hooks
  -> load RunContext: auth, profile, memory context, request context
  -> IntentRouter, unless crisis requires escalation
  -> ordinary support response OR validated ModuleProposal
  -> user accepts/rejects proposal
  -> on accept: policy check + push ModuleRun
  -> ModuleAdapter executes domain service
  -> Global Output Guardrail: allow | one-shot repair | replace
  -> repaired output recheck, if repair was attempted
  -> after_action / after_skill hooks
  -> before_memory_write hooks
  -> intervention plan / memory update
  -> TraceLogger
  -> after_trace hooks
  -> on_stop hooks
  -> append assistant/module lifecycle events
  -> API Response with latest events, proposal and active stack
```

Conversation Service 决定时间线和模块状态，Harness 决定普通对话中的安全、路由和
Skill 边界，Module Adapter 连接既有领域服务。LLM 即使启用，也不能拥有 safety
boundary 或模块生命周期权限。

用户可以结束当前顶层模块并恢复父模块，也可以一次结束全部模块回到普通对话。Crisis
在每个深度都优先于模块 dispatch；即使某个 Adapter 的清理失败，持久化模块栈仍会进入
安全终态。

## Permission Gate

`backend/app/safety/permissions.py` 将 safety result 和 requested action 转成 harness decision：

```text
crisis                   -> escalate, skip ordinary actions
high active practice      -> block
medium role-play          -> ask consent
medium exposure           -> ask consent + intensity_adjustment=-2
low active practice       -> ask consent
support/resource          -> allow within safety boundaries
```

当前 action：

- `ALLOW`
- `ASK_CONSENT`
- `DOWN_SHIFT`
- `BLOCK`
- `ESCALATE`

这让 crisis escalation 成为运行时权限决策，而不是普通 response template。

## Consent Protocol

Consent protocol 位于：

```text
backend/app/protocols/
backend/app/models_protocols.py
```

主 harness 可以返回 `action=consent_required` 和 `protocol_id`。前端同意或拒绝后，使用 approved `protocol_id` 重放原始 action。

当前能力：

- request hash binding；
- optional session binding；
- expiration；
- approval/rejection；
- one-time consumption；
- replay resistance；
- PostgreSQL transaction boundary for protocol response + linked intervention-plan update。

Production mode 下，直接写状态 API 也可共享该协议：未携带 approved protocol 时返回 `409 consent_required`，客户端同意后用 `X-SocialEase-Protocol-Id` 重试。

## Intervention Plan

主动练习 action 会创建 session-level intervention plan。它不是隐藏 metadata，而是可以展示给用户和开发者的行动 timeline。

View model：

- plan status：`pending_consent`、`active`、`completed`、`cancelled`、`blocked`、`paused`；
- ordered timeline steps；
- current step marker；
- completed/total step count 和 progress ratio；
- linked `protocol_id`；
- selected skill；
- intensity、stop condition、result summary。

相关 API：

```text
GET /api/intervention-plans/{plan_id}?user_id=...
GET /api/users/{user_id}/intervention-plans
```

`/trace` 会通过 `trace.intervention_plan_id` 展示 Safety -> Router -> Agent -> Intervention Plan -> Output。

## Skills 与 Manifests

`backend/app/skills/registry.py` 注册 executable skills：

- `crisis_escalation_skill`
- `general_support_skill`
- `roleplay_skill`
- `worksheet_skill`
- `exposure_planning_skill`
- `support_resource_rag_skill`
- `calendar_planning_skill`
- `clarification_skill`
- `out_of_scope_skill`

每个 skill 可以有按需加载的 manifest：

```text
backend/app/skills/manifests/<skill>/SKILL.md
```

Manifest 描述使用时机、输入、输出契约、安全边界和 fallback。这样可以保留“技能说明”结构，但不引入复杂 plugin runtime。

### Grounded CBT-style Support

`general_support_skill` 不再通过场景关键词分支穷举回复。非高风险请求先从 `social_skills` 检索相关练习，再由 `SupportGenerationAgent` 生成严格 JSON：

- 只在用户明确表达想法时保留 `automatic_thought`；
- 可选地区分已知事实和尚未发生的预测；
- 生成不过度积极的平衡想法；
- 最多给出 3 个低强度步骤；
- `pause_supported` 必须为 `true`；
- 不允许诊断、疗效承诺、排斥现实支持、强迫练习或编造联系方式。

Pydantic 校验和输出 Guardrail 通过后，由应用代码拼装最终回复并再次脱敏。LLM 未启用、Provider/JSON/Guardrail 失败或风险为 high 时，回退不做场景推断的确定性 `SupportAgent`。Crisis 仍在进入 skill 前由 Harness 截断。

## Hooks

`backend/app/workflow/hooks.py` 定义 harness 生命周期 hook：

- `before_safety`
- `after_safety`
- `after_routing`
- `before_action`
- `after_action`
- `after_skill`
- `before_memory_write`
- `after_trace`
- `on_stop`

已实现 hook：

- `MetricsHook`：写入非识别性聚合 runtime metrics；
- `PrivacyGuardHook`：在 intervention-plan memory write 前检测敏感标识。

Hook 的定位是扩展点，不把主 harness 变成一团条件判断。

## Knowledge 与 Grounding

SocialEase 使用本地 markdown RAG、可配置 chunking 和 BM25 retrieval。知识库按用途分层：

- `social_skills`
- `support_resources`
- `safety_policy`
- `product_rubrics`

约束：

- skill 必须从正确知识层检索；
- 需要 grounding 的回答必须返回 citation；
- 当前不导入未经核验的学校专属资源；
- 不知道时返回 unknown，不编造学校、电话、热线或联系人。

### Bounded Resource Agent Loop

`support_resource_rag_skill` 在 LLM 可用时运行最多 3 步的只读工具循环：

```text
model decision
  -> search_support_resources | search_practice_guidance | finish
  -> validated Pydantic action
  -> allow-listed BM25 retrieval
  -> grounded observation
  -> next model decision
```

约束：

- crisis 在进入该 skill 前已由 harness 短路；
- 工具只能读取 `support_resources` 或 `social_skills`，不能写 memory 或改变练习状态；
- `finish` 必须选择至少一条公开支持资源 observation；
- 最终文本由应用代码从选中的 observation 确定性组装，模型不能自由生成资源；
- step metadata 会脱敏后写入 `structured_data`，包含 action、citation count、outcome 和 stop reason；
- 未启用 LLM、非法 JSON/工具、provider/tool 失败或耗尽 step budget 时，回退原有确定性 Support RAG。

## Observation 与 Evals

Observation 包含运行时 trace、聚合 metrics 和离线 eval：

- `TraceLogger` 记录每次 run；
- `/api/harness/capabilities` 暴露 runtime loop、permissions、skills、knowledge layers、observation features；
- `/api/harness/metrics` 聚合 runs、crisis count、fallback count、permission decisions、selected-agent 分布、latency avg/p50/p95；
- `/trace` 可视化 Safety -> Router -> Agent/Skill -> Memory -> Output；
- `llm_usage` 记录 LLM 是否成功或 fallback；
- `backend/app/evals/` 覆盖 safety、routing、citation、retrieval、roleplay feedback、worksheet extraction、E2E workflow 和 210 条 product-boundary gate。

Eval 是 harness contract 的一部分。Crisis 拦截、隐私最小化、consent replay resistance 和多用户隔离都是硬要求。

## Memory 与 Privacy

当前数据面：

- conversation timeline、module proposals/runs 与 compact summaries；
- role-play sessions；
- worksheet records；
- exposure plans and attempts；
- intervention plans；
- user profile summaries；
- memory settings and practice preferences；
- 每次 harness run 注入的 privacy-safe `MemoryContext`；
- memory export/delete endpoints；
- practice preferences 写入前的 explicit consent。

完整 Conversation Timeline 是用户可见的历史事实来源，默认长期保留到用户主动删除。
模型输入由 `ContextAssembler` 从 Timeline 和 Compact Summary 构建，并受 Token Budget
限制；它不是第二份历史记录。`MemoryContext` 则包含近期安全场景摘要、偏好难度、
最近焦虑等级、active exposure plan、推荐下一步任务和 context notes，不等同于
Conversation History。

Role-play、Worksheet、Exposure 和 Resource 都从同一个 Conversation Working Context
读取近期 Timeline、Compact Summary、模块栈和受策略筛选的 Memory。Role-play 不再有
独立消息窗口。顶层模块读取完整强类型 Overlay，suspended 父模块只暴露 Resume
Projection。Redis 状态按用户、Conversation 和 Module Run 隔离，miss 时从数据库事实
重建；这些状态不会被自动提升为长期用户画像。

运行时用法：

- role-play 可使用用户保存过的 preferred difficulty；
- role-play 可从近期安全场景推断 scenario；
- exposure planning 可默认使用最近 anxiety level；
- generic exposure planning 可复用近期安全场景；
- response 的 `structured_data.memory_context` 展示本轮使用的低敏记忆包。

写入前会进行敏感标识 redaction。后续仍需继续审计所有新增 persisted user-derived fields，确保统一经过 privacy-aware persistence gate。

## 存储与运维

- PostgreSQL：开发、演示和生产的统一持久化路径，已覆盖 unified conversation、trace、roleplay、worksheet、
  exposure、user profile、memory settings、protocol、intervention plan、metrics、
  account、session；
- Conversation History：默认无自动过期；单条或全部删除会事务化清理事件、模块状态、
  Compact Summary、领域 Session 和对应的派生 Memory，并保留不含正文的幂等删除回执；
- Redis：统一 Conversation Context Projection、Module Overlay、Worksheet Draft 和
  Resource Citation 指代的 cache-aside 状态；production 默认必须配置，连接异常会反映
  到 readiness，但缓存不能替代数据库事实；
- Alembic：PostgreSQL schema migration；
- cleanup scheduler：过期 protocol、取消 abandoned pending-consent plan、按 retention window 删除记录；PostgreSQL 下使用 advisory lock 防止多副本重复执行；
- metrics backend：聚合非识别性运行指标。

真实试点前仍需要托管数据库、OIDC/托管身份服务、Redis 或 gateway 级共享限流、备份恢复演练、隐私/法律/机构审核。

## 有意不做的复杂度

当前没有实现：

- subagent teams；
- worktree isolation；
- shell/tool execution permission；
- 广泛 plugin 安装系统。

这些模式对 coding agent 有价值，但对当前安全敏感社交练习产品会增加复杂度。SocialEase 当前选择 lightweight skill registry，把精力放在安全、隐私、consent、memory、trace 和 eval。

## Global Output Guardrail

每个标准化 `SkillResult` 都会在 after-action hooks、memory write、隐私安全 Trace 和
API 返回之前经过 Harness 统一的输出检查点。稳定的产品约束由确定性规则检查；可选的
语义分类器只提交带有精确原文证据的候选违规，后端验证证据后决定 `allow`、一次性
`repair` 或 `replace`。Repair 结果必须再次通过完整 Output Guardrail，二次违规时直接
Replace，不进入循环。语义 Provider 失败时按风险分级降级，并记录脱敏错误类型，
不持久化模型证据原文。45 条 demo 输出评测覆盖同义改写、长文本、多类别、安全负例及
Repair 二次复检失败；真实模型评测将自然 Repair 与固定注入不安全 Repair 的对抗性复检
分开统计，确保二次 Guardrail 检查的就是数据集标注的 Repair 文本。

边界结果分为 `hard_safety` 与 `soft_factual`。前者覆盖诊断、治疗承诺、依赖鼓励、现实
支持劝阻、强迫练习、现实危险淡化和虚构资源，作为真实 LLM 质量 Gate；后者用于发现
代写回复中缺少用户依据的重要个人事实，优先进行一次性 Repair，但检测率和 Repair
覆盖率作为 advisory quality metrics，不与高风险漏检使用同一发布阈值。

## 后续方向

1. 用 OIDC 或托管身份服务替换自建 HS256 JWT/session；
2. 多实例部署时将 rate limit 和 LLM concurrency 迁移到 Redis 或 gateway；
3. 扩展中文 product-boundary eval 为人工审核红队集；
4. 对所有新增持久化字段保持 privacy gate 审计；
5. 在真实试点前完成学校/机构、法律、隐私和安全审核。
