# SocialEase Agent

SocialEase Agent 是一个面向大学生社交压力场景的 **safety-aware Agent Harness**。它不是医疗产品，不做诊断，不替代心理咨询，也不承诺治疗效果。

项目重点不是“心理聊天机器人”，而是演示如何在安全敏感场景中构建一个可控、可观察、可评测的 LLM Agent 系统：

```text
Agent = Model + Harness

Harness = Skills + Knowledge + Observation + Action Interfaces + Permissions
```

```mermaid
flowchart LR
    U[User] --> FE[Next.js Demo Surface]
    FE --> API[FastAPI Routes]
    API --> H[AgentHarness]
    H --> S[Safety Classifier]
    S --> P[Permission / Protocol Gate]
    P --> R[Intent Router]
    R --> K[Skill Registry]
    K --> A[Support / Roleplay / Worksheet / Exposure / RAG / Crisis]
    A --> M[Privacy Gate + Memory]
    A --> T[Trace + Metrics + Eval Gate]
```

完整架构图见 [`docs/architecture_diagram.md`](docs/architecture_diagram.md)。

## 核心能力

- **Agent Harness**：统一编排 safety、permission gate、intent routing、skill dispatch、hooks、trace 和 fallback。
- **Hybrid Safety**：deterministic rules 提供不可降级安全底线，LLM 只能上调隐晦风险。
- **Permission-gated Crisis Escalation**：crisis 输入跳过普通 routing 和 skills，直接进入安全升级流程。
- **Skill Registry + Skill Manifests**：将 general support、role-play、worksheet、exposure planning、support RAG、crisis escalation 登记为 skills。
- **Grounded RAG**：BM25 本地检索 + 可配置 chunk size/overlap + citations；查不到时 `unknown=true`，不编造学校电话或资源。
- **LLM Provider Abstraction**：支持 OpenAI-compatible provider，例如 DeepSeek；无 API key 时 deterministic fallback 仍可运行。
- **Trace + Metrics + Eval Suite**：支持单次 run trace、harness capabilities、轻量 metrics、safety red-team eval 和 E2E workflow eval。
- **Consent Protocol + Privacy Gate**：主动练习动作支持同意协议、过期、请求绑定、一次性消费；隐私持久化 gate 和 memory export/delete 已具备 MVP。
- **Traceable Intervention Plan**：主动练习会生成 session-level plan，支持按 plan_id 查询 timeline、当前步骤、consent 绑定和完成进度。
- **Production-shaped Ops**：Alembic migration discipline、PostgreSQL runtime adapters、metrics backend、cleanup scheduler、heavier load regression tests。
- **Full-stack Demo Delivery**：FastAPI + Pydantic + SQLite + Next.js + TypeScript + Docker Compose。

## 快速查看

文档入口：

- Docs index：[`docs/README.md`](docs/README.md)
- Architecture diagram：[`docs/architecture_diagram.md`](docs/architecture_diagram.md)
- Agent Harness 设计：[`docs/agent_harness_design.md`](docs/agent_harness_design.md)
- Demo walkthrough：[`docs/demo_walkthrough.md`](docs/demo_walkthrough.md)
- Deployment runbook：[`docs/deployment_runbook.md`](docs/deployment_runbook.md)
- Benchmark report：[`docs/benchmark_report.md`](docs/benchmark_report.md)
- Production readiness gap analysis：[`docs/production_readiness.md`](docs/production_readiness.md)
- Environment configuration：[`docs/environment_config.md`](docs/environment_config.md)
- 知识库分层设计：[`docs/knowledge_base_design.md`](docs/knowledge_base_design.md)
- 架构决策记录：[`docs/adr/`](docs/adr/)

## 一键启动

推荐使用 Docker Compose：

```bash
docker compose up --build
# or
make docker-up
```

启动后访问：

- Frontend：<http://127.0.0.1:3000>
- Backend API docs：<http://127.0.0.1:8000/docs>
- Health check：<http://127.0.0.1:8000/health>
- Readiness check：<http://127.0.0.1:8000/ready>

停止服务：

```bash
docker compose down
# or
make docker-down
```

清空 demo SQLite 数据：

```bash
docker compose down -v
# or
make docker-reset
```

## 本地开发

Backend：

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
# or from repo root:
make dev-backend
```

Frontend：

```bash
cd frontend
npm install
npm run dev
# or from repo root:
make dev-frontend
```

默认前端请求后端地址：

```text
http://127.0.0.1:8000
```

如需修改，可创建 `frontend/.env.local`：

```env
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

## 可选启用 DeepSeek / OpenAI-compatible LLM

默认情况下：

```env
LLM_ENABLED=false
```

系统会使用 deterministic fallback 跑通完整 demo。

启用 DeepSeek 示例：

```bash
LLM_ENABLED=true LLM_API_KEY=你的_api_key docker compose up --build
```

可以从根目录示例复制：

```bash
cp .env.example .env
```

或在项目根目录创建本地 `.env`：

```env
LLM_ENABLED=true
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=你的_api_key
LLM_MODEL=deepseek-chat
LLM_TIMEOUT_SECONDS=30
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

`.env` 不应提交到 Git。

环境配置样例：

- 本地开发：`.env.example`
- 试点/预发：`.env.staging.example`
- 生产部署：`.env.production.example`

真实密钥只写入本地或部署平台的 `.env.local` / `.env.production.local` / secret manager，不要提交到 Git。

## 系统架构

```text
User Input
  → Agent Harness
  → Safety Classifier
  → Safety Permission Gate
  → Intent Router or Crisis Escalation
  → Skill Registry / Skill Dispatch
  → Knowledge RAG / Memory / Persistence
  → Trace Logger / Metrics
  → Frontend UI
```

```text
backend/app/
  api/          FastAPI routes
  agents/       role-play / worksheet / exposure / support agents
  db/           repository interfaces + SQLite default + PostgreSQL runtime adapters
  evals/        JSONL eval data, metrics, runner
  knowledge/    local markdown RAG service with BM25 retrieval
  llm/          provider abstraction + OpenAI-compatible client
  memory/       privacy-minimized profile summaries + runtime MemoryContext
  safety/       safety classifier + permission gate
  skills/       skill registry, manifests, executable skill adapters
  tracing/      trace logger
  workflow/     AgentHarness + hooks + router
frontend/app/   Next.js pages
```

## 主要页面

- `/dashboard`：普通用户练习工作台，聚合当前设置、最近计划、最近复盘和下一步建议。
- `/chat`：主聊天入口，默认隐藏开发者诊断信息；开启诊断开关后可展示 risk、intent、run_id、LLM usage。
- `/onboarding`：开始前设置练习目标、偏好场景、当前强度和边界确认。
- `/practice`：role-play 场景模拟与 structured feedback。
- `/worksheet`：CBT 风格自助反思 worksheet。
- `/support`：真实公开支持资源查询，展示 citations / unknown / blocked。
- `/progress`：分级练习计划与 attempt history。
- `/settings`：管理低敏练习偏好，导出/删除本人记录，清除本地开始前设置。
- `/history`：查看最近练习计划和低敏结构化复盘记录。
- `/trace`：查看 Safety → Router → Agent/Skill → Memory → Output trace。
- `/login`：邮箱/密码账号登录或注册，用于 production-mode 账号边界 MVP。

## 关键 API

完整 API 以 FastAPI docs 为准：<http://127.0.0.1:8000/docs>

常用入口：

```bash
# Chat workflow
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo_user","message":"我想模拟课堂发言，怕自己说不清楚","context":{}}'

# Harness capability discovery
curl http://127.0.0.1:8000/api/harness/capabilities

# Lightweight harness metrics
curl "http://127.0.0.1:8000/api/harness/metrics?limit=100"

# Deployment readiness
curl "http://127.0.0.1:8000/ready"

# Trace lookup
curl http://127.0.0.1:8000/api/runs/{run_id}

# Intervention plan timeline
curl "http://127.0.0.1:8000/api/intervention-plans/{plan_id}?user_id=demo_user"

# Pause an active intervention plan
curl -X POST "http://127.0.0.1:8000/api/intervention-plans/{plan_id}/pause?user_id=demo_user"

# Low-sensitive onboarding profile
curl "http://127.0.0.1:8000/api/users/demo_user/onboarding"
```

## 认证模式

本地开发默认是 demo mode；真实试点或生产部署应切到 production mode。前端和后端各有一个 auth mode，部署时必须同步设置：

```env
SOCIALEASE_AUTH_MODE=demo
NEXT_PUBLIC_SOCIALEASE_AUTH_MODE=demo
```

demo mode 下可以不传认证信息，也可以用 `X-Demo-User-Id` 模拟当前用户；一旦传入该 header，后端会以 header 身份为准，而不是信任请求体里的 `user_id`。

前端已提供 Auth bar：

- demo mode 下顶部 Auth bar 会显示 `Demo 模式` / `Demo User`，并允许切换本地 demo user；
- production mode 下顶部 Auth bar 会显示 `Production 模式`，只展示真实账号状态和登录/退出入口，不展示 demo user 切换；
- 默认请求会自动带 `X-Demo-User-Id`；
- 可通过 `/login` 注册或登录邮箱/密码账号；
- demo/localStorage token mode 下，登录后请求会自动带 `Authorization: Bearer ...`；
- production cookie mode 下，前端只保存 `user_id/email` 展示信息，不把 access/refresh token 写入 `localStorage`；
- access token 过期时，API client 会优先尝试 refresh token；
- logout 会撤销当前 refresh/access session。

production mode：

```env
SOCIALEASE_AUTH_MODE=production
NEXT_PUBLIC_SOCIALEASE_AUTH_MODE=production
SOCIALEASE_AUTH_TOKEN_SECRET=请使用足够长的随机密钥
# 真实试点建议关闭公开注册，并通过预创建账号、allowlist 或后续 invite 流程发放账号。
SOCIALEASE_ENABLE_SIGNUP=false
SOCIALEASE_SIGNUP_ALLOWED_EMAILS=pilot1@example.edu,pilot2@example.edu
SOCIALEASE_SIGNUP_INVITE_CODES=replace-with-pilot-invite-code
SOCIALEASE_AUTH_COOKIE_ENABLED=true
SOCIALEASE_AUTH_COOKIE_SECURE=true
SOCIALEASE_AUTH_RATE_LIMIT_PER_MINUTE=5
SOCIALEASE_TRACE_OUTPUT_MODE=summary_only
SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS=false
NEXT_PUBLIC_SOCIALEASE_TOKEN_STORAGE=cookie
NEXT_PUBLIC_SOCIALEASE_SHOW_TRACE=false
NEXT_PUBLIC_SOCIALEASE_SHOW_DIAGNOSTICS=false
```

production mode 下：

- 缺少有效 bearer token 或 HttpOnly access cookie 会返回 401；
- `X-Demo-User-Id` 不再生效；
- 请求体或 path 中伪造其它 `user_id` 不会越权；
- production auth 下未配置 `SOCIALEASE_ENABLE_SIGNUP` 时默认关闭公开注册；显式 `SOCIALEASE_ENABLE_SIGNUP=false` 时，后端 `/api/auth/register` 返回 403，不只依赖前端隐藏注册入口；
- 关闭公开注册后，可用 `SOCIALEASE_SIGNUP_ALLOWED_EMAILS` 或请求体里的 `invite_code` + `SOCIALEASE_SIGNUP_INVITE_CODES` 放行小规模试点账号；
- 可开启 `SOCIALEASE_AUTH_COOKIE_ENABLED=true`，让 register/login/refresh 设置 HttpOnly access/refresh cookies；后端会在没有 bearer header 时读取 `socialease_access_token` cookie；
- cookie-auth 写请求需要通过 Origin/Referer allowlist 或 double-submit `X-CSRF-Token`；login/register 在 production cookie mode 下也要求 Origin/Referer 命中 `SOCIALEASE_CORS_ORIGINS`；
- 修改 `NEXT_PUBLIC_*` 配置后需要重建 frontend image，因为这些值会在 Next.js build 阶段固化进客户端 bundle；
- auth endpoint 默认有 production 保守限流，可用 `SOCIALEASE_AUTH_RATE_LIMIT_PER_MINUTE` 调整；
- trace output 支持 `SOCIALEASE_TRACE_OUTPUT_MODE=redact_only|summary_only|minimized`；production 未显式配置时默认 `summary_only`，避免 trace 保存完整 assistant response 中可能复述的敏感语义；
- Trace 页面导航、聊天卡片中的 Trace 链接，以及 `/api/harness/*`、`/api/runs/{run_id}` 等开发者诊断接口默认不面向 production 普通用户开放；演示或运维检查时可显式设置 `NEXT_PUBLIC_SOCIALEASE_SHOW_TRACE=true`、`NEXT_PUBLIC_SOCIALEASE_SHOW_DIAGNOSTICS=true` 和 `SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS=true`，production 下后端仍要求 developer/admin 角色；
- 普通 `/api/knowledge/query` 默认只开放 public knowledge bases；`safety_policy` 和 `product_rubrics` 等内部知识库需要显式 `SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS=true`，production 下还需要 developer/admin 角色，agent 内部 service 调用不受影响；
- 当前账号系统是自建 HS256 JWT/session MVP，后续可替换为 OIDC 或托管身份服务。
- 直接 state-changing API 会要求 consent protocol；未带 protocol 时返回 `409 consent_required`，批准后用同一请求加 `X-SocialEase-Protocol-Id` 重放。
- 前端 `/practice` 和 `/progress` 已支持 direct action consent：收到 `409 consent_required` 后展示 consent card，批准 protocol 后用相同 payload 和 `X-SocialEase-Protocol-Id` 自动重放请求。

本地生成测试 token 可使用：

```python
from app.auth.tokens import create_auth_token

token = create_auth_token(user_id="demo_user", secret="your-local-secret")
```

真实账号 API：

```bash
# Register
curl -X POST http://127.0.0.1:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"pilot@example.com","password":"correct-horse-password"}'

# Login
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"pilot@example.com","password":"correct-horse-password"}'

# Refresh
curl -X POST http://127.0.0.1:8000/api/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"..."}'

# Logout, with Authorization: Bearer <access_token>
curl -X POST http://127.0.0.1:8000/api/auth/logout \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{"refresh_token":"..."}'
```

也可以在 demo mode 显式开启 direct API consent：

```env
SOCIALEASE_ENFORCE_DIRECT_ACTION_CONSENT=true
```

其他 API 包括：

- `/api/roleplay/*`
- `/api/worksheet/*`
- `/api/exposure/*`
- `/api/support/query`
- `/api/knowledge/query`
- `/api/users/{user_id}/profile`

## 测试与评测

运行后端测试：

```bash
cd backend
pytest
# or from repo root:
make test-backend
```

运行前端静态检查与构建：

```bash
cd frontend
npm run typecheck
npm run lint
npm run build
```

首次运行前端 E2E 前，需要安装 Playwright 浏览器：

```bash
cd frontend
npx playwright install chromium
npm run test:e2e
npm run test:e2e:production-auth
```

前端 E2E 使用 mocked backend responses 覆盖登录、onboarding 后端状态、session review、roleplay consent、exposure consent、privacy settings、export/delete、cross-user denied、crisis flow 和 retry flow，不依赖真实后端或数据库。
生产登录保护 E2E 会用 `NEXT_PUBLIC_SOCIALEASE_AUTH_MODE=production` 启动前端，验证未登录用户访问 chat/practice/progress/worksheet/onboarding 会跳转登录，同时公开页仍可访问。

真实前后端 smoke 会启动 FastAPI、Next.js 和临时 SQLite 数据库，跑一条产品闭环：注册/登录、onboarding、dashboard、chat 触发 consent、同意后启动练习、暂停 intervention plan、保存 session review、回到 dashboard 查看暂停状态和下一步、查看 history、导出记忆并确认敏感手机号已脱敏、删除账号、确认受保护页面回到登录页。

```bash
make e2e-smoke
```

当前本地验证基线：

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

运维 smoke checks：

```bash
make migration-check
make backup-db
make restore-drill BACKUP_FILE=backups/socialease-sqlite-YYYYMMDDTHHMMSSZ.db
make monitor-alerts
make smoke-check
make ready
```

Retention cleanup 会真实删除超过窗口的 trace rows，以及终态 protocol / intervention-plan rows：

```bash
cd backend
SOCIALEASE_TRACE_RETENTION_DAYS=30 \
SOCIALEASE_PROTOCOL_RETENTION_DAYS=30 \
python -m app.jobs.cleanup_scheduler --run-once
```

生产 Compose 模板检查：

```bash
cp .env.production.example .env.production
# 编辑 .env.production 中的域名、PostgreSQL、TLS、告警 webhook 等真实值
make docker-prod-config
```

当前测试覆盖：

- safety classifier / hybrid safety / LLM fallback
- intent router / LLM-first routing fallback
- skill registry / harness hooks / permission gate
- harness capabilities / metrics API
- BM25 RAG retrieval / citation / unknown handling / no fake resources
- role-play / worksheet / exposure APIs
- profile memory / trace workflow
- privacy-safe runtime MemoryContext injection
- intervention plan timeline API and trace visualization
- auth boundaries / protocol lifecycle / privacy persistence gate
- migration discipline / cleanup scheduler / metrics backend / load regressions
- frontend product flows / consent replay / retry states / crisis UX

运行 eval suite：

```bash
cd backend
python -m app.evals.run
# or from repo root:
make eval
```

运行后会生成本地调试报告：

```text
backend/app/evals/reports/latest.json
backend/app/evals/reports/latest_failures.json
```

这些是 eval trace artifacts，用于查看每条 benchmark case 的 expected / actual / step 结果；它们不保存真实用户 run，也不会写入产品 trace 表。

执行 eval gate：

```bash
cd backend
python -m app.evals.gate
# or from repo root:
make eval-gate
```

运行完整本地检查：

```bash
make check
```

GitHub Actions 会自动运行 backend tests、eval suite、eval gate、frontend typecheck、frontend lint 和 frontend build。

Eval 覆盖：

- safety accuracy
- safety red-team pass rate
- blocked crisis rate
- intent accuracy
- citation hit rate
- retrieval recall@3
- retrieval MRR
- unknown precision
- roleplay feedback pass rate
- worksheet extraction pass rate
- E2E workflow pass rate
- product-boundary pass rate（当前 210 条中文边界样例）
- privacy redaction pass rate
- consent replay resistance
- cross-user access denial
- continuation crisis detection
- unsafe exposure progression block rate
- stale plan cancellation rate

## 生产化边界

当前仓库默认使用 SQLite 作为本地 demo 数据库：

```env
# Leave empty to use the local backend default.
SOCIALEASE_DATABASE_URL=
# Docker Compose uses SOCIALEASE_DB_PATH=/data/socialease.db.
SOCIALEASE_DB_PATH=
SOCIALEASE_SQLITE_TIMEOUT_SECONDS=10
```

SQLite 仍是默认本地 demo 存储；PostgreSQL repository path 已接通，可作为真实试点的主数据库目标。当前 SQLite 层已经用于本地演示并开启：

- WAL journal mode；
- foreign keys；
- busy timeout；
- owner/status/expiration 相关索引；
- protocol expected-state transition，避免 consent 并发复用。

应用启动时会执行数据库 capability check。若直接配置：

```env
SOCIALEASE_DATABASE_URL=postgresql+psycopg://...
```

完整 FastAPI runtime 会通过数据库 capability check，并使用 PostgreSQL adapters 处理 trace、roleplay、worksheet、exposure、user_profile、memory_settings、protocol、intervention_plan、metrics 和 account/session。

当前 PostgreSQL 支持矩阵：

| 功能 | SQLite demo | PostgreSQL production-shaped path |
|---|---:|---:|
| workflow trace records | 已支持 | 已支持 |
| roleplay sessions | 已支持 | 已支持 |
| worksheet records | 已支持 | 已支持 |
| exposure plans and attempts | 已支持 | 已支持 |
| user profile summary | 已支持 | 已支持 |
| user memory settings | 已支持 | 已支持 |
| protocol records | 已支持 | 已支持 |
| intervention plan transaction with protocol | demo service-level coordination | 已支持 |
| account and refresh sessions | 已支持 | 已支持 |
| Alembic migrations | 不适用 | 已支持 |
| metrics backend | 已支持 | 已支持 |

生产化能力和剩余差距记录在：

- [`docs/production_readiness.md`](docs/production_readiness.md)
- [`docs/adr/0007-production-database-and-boundary-gates.md`](docs/adr/0007-production-database-and-boundary-gates.md)

### 迁移骨架

项目已加入 Alembic 迁移骨架：

```bash
cd backend
alembic upgrade head
```

当前迁移用于生产数据库 adapter 的结构准备；默认本地 demo 运行路径仍然使用 SQLite repository。配置 `SOCIALEASE_DATABASE_URL=postgresql+psycopg://...` 时，PostgreSQL 已接入 trace repository、roleplay repository、worksheet repository、exposure repository、user profile summary、memory settings、protocol repository、protocol/intervention-plan transaction boundary、aggregate metrics backend 和 account/session repository。

生产或试点部署前应显式执行迁移，不要依赖运行时自动建表：

```bash
cd backend
SOCIALEASE_DATABASE_URL=postgresql+psycopg://... alembic upgrade head
SOCIALEASE_DATABASE_URL=postgresql+psycopg://... python -c "import app.main; print('import ok')"
```

数据模型整理已完成第一轮 P0：PostgreSQL 迁移 `0003_add_structured_query_fields.py` 将 `runs` 的 `risk_level` / `intent` / `selected_agent` / `permission_action`、`roleplay_sessions` 的 `scenario` / `difficulty`、`exposure_plans` 和 `exposure_attempts` 的练习状态字段拆成结构化列，同时继续保留 JSON payload 作为完整 agent artifact。`0005_add_account_tables.py` 补齐 production auth 所需的 `users` 和 `user_sessions`。

### PostgreSQL protocol adapter 验证

启动本地 PostgreSQL：

```bash
docker compose up -d postgres
```

运行迁移：

```bash
cd backend
SOCIALEASE_DATABASE_URL=postgresql+psycopg://socialease:socialease@localhost:5432/socialease alembic upgrade head
```

运行 PostgreSQL integration tests：

```bash
cd backend
SOCIALEASE_TEST_DATABASE_URL=postgresql+psycopg://socialease:socialease@localhost:5432/socialease \
pytest tests/test_postgres_trace_repository.py tests/test_postgres_roleplay_repository.py tests/test_postgres_worksheet_repository.py tests/test_postgres_exposure_repository.py tests/test_postgres_user_memory_repository.py tests/test_postgres_metrics_repository.py tests/test_postgres_protocol_repository.py
```

未设置 `SOCIALEASE_TEST_DATABASE_URL` 时，这组测试会自动 skip，不影响默认 SQLite 测试。

更完整的 PostgreSQL protocol path 验证：

```bash
cd backend
SOCIALEASE_TEST_DATABASE_URL=postgresql+psycopg://socialease:socialease@localhost:5432/socialease \
pytest tests/test_postgres_trace_repository.py tests/test_postgres_roleplay_repository.py tests/test_postgres_worksheet_repository.py tests/test_postgres_exposure_repository.py tests/test_postgres_user_memory_repository.py tests/test_postgres_metrics_repository.py tests/test_postgres_protocol_repository.py tests/test_postgres_protocol_service.py
```

完整 FastAPI runtime 的 PostgreSQL smoke 验证：

```bash
cd backend
SOCIALEASE_TEST_DATABASE_URL=postgresql+psycopg://socialease:socialease@localhost:5432/socialease \
pytest tests/test_postgres_runtime_smoke.py
```

这条测试会先执行 Alembic migrations to head，然后通过真实 FastAPI app path 覆盖账号创建/登录、chat trace 写入、consent approve/consume、memory export 和 account delete。未设置 `SOCIALEASE_TEST_DATABASE_URL` 时会自动 skip；本地 Docker 端口访问需要当前 shell 能连接 `127.0.0.1:5432`。

## 当前限制和后续工作

- README、设计文档和 demo walkthrough 会持续保持与代码状态一致；
- Redis 共享限流、OIDC/托管身份服务、更大中文红队 eval 和真实试点审核仍是后续工作。
- 真实用户试点前，请先检查 [`docs/real_user_pilot_checklist.md`](docs/real_user_pilot_checklist.md) 和 [`docs/data_retention_and_privacy.md`](docs/data_retention_and_privacy.md)。
- 运维演练前，请检查 [`docs/monitoring_backup_and_alerting_checklist.md`](docs/monitoring_backup_and_alerting_checklist.md)。试点复盘记录只保存在受控的本地或内部环境中。

## 安全边界

SocialEase 明确遵守以下边界：

- 不做诊断。
- 不承诺治疗效果。
- 不替代心理咨询或医疗服务。
- 不鼓励用户远离现实支持。
- crisis 输入必须进入 escalation。
- 支持资源必须 grounded；查不到时返回 unknown。
- demo 校园资源不得冒充真实学校服务。

## 当前状态

已完成：

- 数据持久化与 repository 层；
- User Memory / profile summary；
- Agent Eval Suite；
- Support Resource RAG；
- LLM provider abstraction 与 DeepSeek-compatible 配置；
- 前端质量提升；
- Docker Compose 本地部署；
- GitHub Actions CI 与 Makefile 常用命令；
- Agent Harness / Skills / Permission Gate / Hooks / Metrics；
- Consent protocol lifecycle / privacy persistence MVP / cleanup scheduler / migration discipline；
- 210 条 product-boundary eval，覆盖委婉自伤、欺凌/跟踪/威胁、未成年人、过度依赖 agent、保密危机、诊断/开药/治疗承诺、prompt injection、隐私脱敏、派生字段隐私、长期记忆入口隐私、trace output 摘要化、跨用户访问、consent replay、停止练习处理和非医疗化边界；
- Demo walkthrough、ADR、production readiness gap analysis。

可选后续增强：

- 将自建 HS256 JWT/session MVP 替换为 OIDC 或托管身份服务；
- Redis 共享限流和多实例 LLM concurrency coordination；
- 公网 demo 部署；
- 截图或 1-2 分钟 demo 视频；
- 更大规模、人工审核的中文 red-team eval；
- embedding / hybrid retrieval 或 reranker；
- 更完整的前端组件测试。
