# SocialEase Agent

SocialEase Agent 是一个面向大学生社交压力场景的 **safety-aware Agent Harness**。它不是医疗产品，不做诊断，不替代心理咨询，也不承诺治疗效果。

项目重点不是“心理聊天机器人”，而是演示如何在安全敏感场景中构建一个可控、可观察、可评测的 LLM Agent 系统：

```text
Agent = Model + Harness

Harness = Skills + Knowledge + Observation + Action Interfaces + Permissions
```

## 核心能力

- **Agent Harness**：统一编排 safety、permission gate、intent routing、skill dispatch、hooks、trace 和 fallback。
- **Hybrid Safety**：deterministic rules 提供不可降级安全底线，LLM 只能上调隐晦风险。
- **Permission-gated Crisis Escalation**：crisis 输入跳过普通 routing 和 skills，直接进入安全升级流程。
- **Skill Registry + Skill Manifests**：将 general support、role-play、worksheet、exposure planning、support RAG、crisis escalation 登记为 skills。
- **Grounded RAG**：分层知识库返回 citations；查不到时 `unknown=true`，不编造学校电话或资源。
- **LLM Provider Abstraction**：支持 OpenAI-compatible provider，例如 DeepSeek；无 API key 时 deterministic fallback 仍可运行。
- **Trace + Metrics + Eval Suite**：支持单次 run trace、harness capabilities、轻量 metrics、safety red-team eval 和 E2E workflow eval。
- **Full-stack Demo Delivery**：FastAPI + Pydantic + SQLite + Next.js + TypeScript + Docker Compose。

## 快速查看

- Demo walkthrough：[`docs/demo_walkthrough.md`](docs/demo_walkthrough.md)
- Agent Harness 设计：[`docs/agent_harness_design.md`](docs/agent_harness_design.md)
- Production readiness gap analysis：[`docs/production_readiness.md`](docs/production_readiness.md)
- 架构决策记录：[`docs/adr/`](docs/adr/)
- 知识库分层设计：[`docs/knowledge_base_design.md`](docs/knowledge_base_design.md)

## 一键启动

推荐使用 Docker Compose：

```bash
docker compose up --build
```

启动后访问：

- Frontend：<http://127.0.0.1:3000>
- Backend API docs：<http://127.0.0.1:8000/docs>
- Health check：<http://127.0.0.1:8000/health>

停止服务：

```bash
docker compose down
```

清空 demo SQLite 数据：

```bash
docker compose down -v
```

## 本地开发

Backend：

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend：

```bash
cd frontend
npm install
npm run dev
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

## 系统架构

```text
User Input
  → Agent Harness
  → Safety Classifier
  → Safety Permission Gate
  → Intent Router or Crisis Escalation
  → Skill Registry / Skill Dispatch
  → Knowledge RAG / Memory / SQLite Persistence
  → Trace Logger / Metrics
  → Frontend UI
```

```text
backend/app/
  api/          FastAPI routes
  agents/       role-play / worksheet / exposure / support agents
  db/           repository interfaces + SQLite implementations
  evals/        JSONL eval data, metrics, runner
  knowledge/    local markdown RAG service
  llm/          provider abstraction + OpenAI-compatible client
  memory/       privacy-minimized profile summaries
  safety/       safety classifier + permission gate
  skills/       skill registry, manifests, executable skill adapters
  tracing/      trace logger
  workflow/     AgentHarness + hooks + router
frontend/app/   Next.js pages
```

## 主要页面

- `/chat`：主聊天入口，展示 risk、intent、run_id、LLM usage。
- `/practice`：role-play 场景模拟与 structured feedback。
- `/worksheet`：CBT 风格自助反思 worksheet。
- `/support`：真实公开支持资源查询，展示 citations / unknown / blocked。
- `/progress`：分级练习计划与 attempt history。
- `/trace`：查看 Safety → Router → Agent/Skill → Memory → Output trace。

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

# Trace lookup
curl http://127.0.0.1:8000/api/runs/{run_id}
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
```

当前测试覆盖：

- safety classifier / hybrid safety / LLM fallback
- intent router / LLM-first routing fallback
- skill registry / harness hooks / permission gate
- harness capabilities / metrics API
- RAG citation / unknown handling / no fake resources
- role-play / worksheet / exposure APIs
- profile memory / trace workflow

运行 eval suite：

```bash
cd backend
python -m app.evals.run
```

Eval 覆盖：

- safety accuracy
- safety red-team pass rate
- blocked crisis rate
- intent accuracy
- citation hit rate
- unknown precision
- roleplay feedback pass rate
- worksheet extraction pass rate
- E2E workflow pass rate

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
- Agent Harness / Skills / Permission Gate / Hooks / Metrics；
- Demo walkthrough、ADR、production readiness gap analysis。

可选后续增强：

- 公网 demo 部署；
- 截图或 1-2 分钟 demo 视频；
- 更大规模 red-team eval；
- 用户数据删除 / 导出 API；
- 更完整的前端组件测试。
