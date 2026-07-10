# SocialEase 生产化能力与差距分析

SocialEase 是一个**产品化 Agent 原型**，不是医疗产品，也不是正式心理健康服务。本文记录当前工程基线，以及在真实多用户部署前仍需补齐的安全、隐私、运维和合规工作。

## 当前基线

最近一次产品化检查点：

```text
backend pytest: 297 passed, 26 skipped
eval suite: all metrics passed
eval gate: passed
frontend typecheck: passed
frontend lint: passed
frontend build: passed
frontend E2E: 23 passed
production auth E2E: 16 passed
real frontend/backend smoke E2E: 1 passed
```

## 已实现的生产化控制

### Safety 与 crisis escalation

已实现：

- 显式 crisis 表达的 deterministic safety floor；
- 可选 LLM safety classifier，但只能提高风险等级；
- classifier fallback 不会把疑似 crisis 降级成普通低风险；
- `SafetyPermissionGate` 支持 allow、ask consent、downshift、block、escalate；
- crisis 输入绕过普通 routing 和 skills；
- 非医疗化 crisis response，引导用户联系现实支持；
- safety、red-team、prompt-injection、confidential-crisis、blocked-crisis eval。

真实试点前仍需：

- 更大的多语言红队数据集；
- 覆盖所有状态写入接口的 continuation-turn red-team；
- 人工审核的 crisis escalation protocol；
- 学校/机构特定的响应流程；
- 临床、法律、隐私措辞审核。

### Harness、权限和同意机制

已实现：

- 主 `AgentHarness`：safety、routing、permission、skill dispatch、hooks、memory/update、trace、recovery；
- support、role-play、worksheet、exposure planning、support-resource RAG、crisis escalation 等 executable skills；
- 主动练习 action 的 consent protocol；
- protocol request hash、session binding、过期、同意/拒绝、一次性消费和 replay resistance；
- PostgreSQL protocol 与 intervention-plan 的事务边界；
- 前端覆盖 `consent_required`、roleplay、worksheet、exposure-plan、support-resource、blocked、failed、crisis；
- harness-managed run 自动创建 intervention plan。

真实试点前仍需：

- 使用 OIDC 或托管身份服务替换自建 HS256 JWT/session；
- 根据产品政策扩展高风险 support/practice 的策略组合；
- 为直接写状态 API 补更深的 HTTP-level eval。

### Grounding 与资源完整性

已实现：

- 分层知识库；
- 可配置 markdown chunking；
- BM25 本地检索和 retrieval diagnostics；
- support-resource RAG 只使用 verified public resources；
- citation metadata 包含 source type 和 URL；
- 未检索到时返回 `unknown=true`，不编造资源；
- 演示校园资源与真实公开资源隔离。

真实试点前仍需：

- 经审核的校园资源导入流程；
- 资源 freshness 检查；
- owner/reviewer metadata；
- 定期审核和过期策略。

### Reliability 与 fallback

已实现：

- `BaseLLMClient` 抽象；
- OpenAI-compatible provider adapter；
- 默认 `LLM_ENABLED=false`；
- provider 瞬时失败 retry；
- repeated provider failure circuit breaker；
- routing、role-play、worksheet extraction、safety 的 deterministic fallback；
- `llm_usage.fallback_used` 和 `llm_usage.error_category` metadata；
- skill/tool failure 和 memory-write failure 的 workflow recovery。

真实试点前仍需：

- provider-level monitoring；
- prompt/model versioning；
- rollout/rollback 策略；
- 多 provider failover 策略。

### Observability

已实现：

- `TraceLogger` for individual runs;
- `/api/runs/{run_id}` trace lookup;
- `/api/harness/capabilities` capability discovery;
- `/api/harness/metrics` aggregate non-identifying metrics;
- `MetricsHook` backed by aggregate non-identifying metric events;
- latency average, p50, and p95 metrics;
- runtime metrics for rate-limit hits, LLM concurrency saturation, auth lockout, memory export/delete, and preference changes;
- standalone cleanup scheduler entrypoint;
- `llm_usage` on key LLM-backed nodes;
- deterministic eval suite。

真实试点前仍需：

- 如保留审计 trace，需要更严格的受限访问；
- crisis/fallback spike 的托管告警；
- SLO/SLA 定义；
- Prometheus/OpenTelemetry 或托管 metrics export。

### 数据与隐私

已实现：

- SQLite 本地开发持久化；
- repository interfaces for storage replacement;
- repository factory with SQLite default and PostgreSQL adapters for trace, roleplay, worksheet, exposure, user profile, memory settings, protocol, intervention plan, metrics, account, and session records;
- explicit database runtime capability check with a clear support matrix;
- Alembic migration discipline and PostgreSQL CI migration check;
- first-pass structured PostgreSQL query fields for trace risk/intent, roleplay scenario/difficulty, and exposure plan/attempt state while retaining JSON payloads for full agent artifacts;
- lightweight user profile summary;
- user memory export/delete endpoints;
- account deletion endpoint that revokes sessions and deletes user-owned practice records;
- explicit consent before writing practice preferences;
- privacy persistence gate for selected trace text;
- privacy persistence gate for worksheet source messages, roleplay user turns, exposure previous attempts, and exposure reflections;
- 本地演示 auth mode 与 production signed bearer-token auth mode，支持可选 HttpOnly access/refresh cookies；
- owner-aware API boundaries for trace, memory, protocol, worksheet, roleplay, and exposure access;
- `PrivacyGuardHook` for intervention-plan memory writes with sensitive identifier detection;
- crisis text 不会复制到普通 memory summarization。

真实试点前仍需：

- OIDC 或托管身份服务接入；
- 对新增持久化用户派生字段持续审计；
- 试点 owner 审核 retention window 和删除/匿名化策略；
- 静态和传输加密；
- admin 角色和访问控制；
- privacy impact assessment。

### 评测与测试

已实现：

- pytest 覆盖 safety、routing、RAG、LLM fallback、skills、APIs、hooks、protocols、memory controls、harness behavior；
- eval suite 覆盖 safety、routing、citation、unknown handling、roleplay feedback、worksheet extraction、retrieval metrics、E2E workflow；
- product-boundary eval gate with 210 bundled Chinese boundary cases;
- heavier local load regression tests for concurrency and migration readiness;
- bundled JSONL eval cases for deterministic regression;
- GitHub Actions workflow 覆盖 backend tests、evals、migration check、frontend quality gates。

真实试点前仍需：

- 比当前 deterministic gate 更大的人工审核中文产品边界数据集；
- 长期质量监控；
- 人工评审 rubric 的实际执行记录。

## 部署就绪度

当前适合本地开发、答辩展示和小规模受控测试：

```bash
docker compose up --build
```

真实部署仍需：

- 托管数据库；
- migration 流程；
- connection pooling；
- secret management；
- HTTPS and CORS hardening;
- authentication/authorization；
- observability stack；
- backup/restore；
- 环境配置审核；
- retention/cleanup job 审核。

## 风险声明

SocialEase 不能被表述为医疗产品或危机服务。它展示的是如何在敏感场景中工程化一个安全可控的 LLM agent harness。真实部署前必须经过学校/机构、临床、法律、隐私和运维审核。

## 后续生产化方向

1. 用 OIDC 或托管身份服务替换自建 HS256 JWT/session。
2. 持续统一所有状态写入入口的 permission/protocol gate。
3. 多实例部署时把共享 rate limit 和 LLM concurrency 迁移到 Redis 或 API gateway。
4. 将 210 条 product-boundary eval 扩展成人工审核红队样本。
5. 在真实部署环境接入监控、告警、备份恢复和审核过的 retention window。
6. 真实用户试点前完成法律、隐私、临床和机构审核。
