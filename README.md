# SocialEase Agent

SocialEase Agent 是面向大学生社交压力练习场景的安全可控 Agent 应用。项目重点不是构建“心理聊天机器人”，而是展示如何通过应用级 Harness 约束模型的路由、工具、权限、记忆、安全边界和评测。

> 本项目只提供非医疗化的自助练习和公开资源导航，不做诊断，不承诺治疗效果，也不能替代心理咨询或医疗服务。

## 架构概览

```mermaid
flowchart LR
    U[User] --> FE[Next.js]
    FE --> API[FastAPI]
    API --> H[Agent Harness]
    H --> S[Input Safety]
    S --> R[Intent Router]
    R --> P[Permission / Consent]
    P --> K[Skill Registry]
    K --> A[Support / Role-play / Worksheet / Exposure / Resource Loop / Calendar / Crisis]
    A --> O[Output Guardrail + Repair]
    A --> M[Context + Memory]
    O --> T[Trace + Eval]
    M --> DB[(PostgreSQL / SQLite)]
    M --> Redis[(Redis Task Sessions)]
```

完整设计见 [架构图](docs/architecture_diagram.md) 和 [Agent Harness 设计](docs/agent_harness_design.md)。

## 核心能力

- **Agent Harness**：统一执行 Safety、Intent Routing、Permission、Skill Dispatch、Output Guardrail、Memory Write、Trace 和失败降级。
- **开放输入路由**：显式区分专业 Skill、普通支持、信息不足和领域外请求；低置信度动作先澄清，不把未知输入强塞给业务模块。
- **有界 Skills**：包含普通支持、Role-play、CBT 风格 Worksheet、分级练习、资源导航和 Crisis Escalation；资源导航可运行最多三步的只读工具循环。
- **全局 Guardrails**：输入侧优先识别危机表达；输出侧结合规则与可选 LLM 检查诊断、疗效承诺、依赖诱导、现实支持劝阻和虚构资源，并对可修复问题执行一次 Repair 与二次复检。
- **Permission / Consent**：主动练习和状态变更可要求一次性同意凭证，绑定用户、会话、动作和请求摘要，并防止过期、篡改与重复消费。
- **Calendar MCP**：Calendar Skill 只生成有限期提醒预览；创建、修改和删除通过 owner-bound Consent、幂等键与创建后回读控制。当前内置 Provider 是 Demo，真实厂商通过 `CalendarProvider` Adapter 扩展。
- **Context / Memory**：数据库保存经授权、脱敏的结构化长期状态；Redis 保存带 TTL 的任务状态。Role-play 使用 Token Budget、动态消息窗口和结构化 Compact，Worksheet 与 Support 使用各自的类型化 State。
- **Grounded Retrieval**：本地 BM25 检索返回 Citation；未命中时返回 `unknown`，不编造学校、电话或联系人。
- **Trace / Eval**：记录隐私安全的执行诊断；提供 294 条确定性 Eval、45 条 Output Guardrail 边界用例和可选 DeepEval LLM Judge。

## 项目结构

```text
backend/app/
  workflow/      AgentHarness、Router、Hooks、运行时 Context
  skills/        可执行 Skill 与 Registry
  safety/        输入分类、权限决策和危机升级
  guardrails/    全局 Output Guardrail、Repair 与复检
  memory/        Context Builder、Redis Task State 与 Compact
  knowledge/     BM25 RAG、Citation 与 Unknown 策略
  calendar/      Calendar Provider、MCP Server/Client 与 Tool Contract
  db/            Repository 接口及 SQLite/PostgreSQL Adapter
  tracing/       结构化 Run Trace
  evals/         Eval 数据、Runner、Metrics 与 Gate
  api/           FastAPI Routes
backend/tests/   单元、架构、边界与集成测试
frontend/app/    Chat、Practice、Worksheet、Progress 等页面
docs/            架构、部署、隐私、知识库与 ADR
```

## 快速启动

### 方案 A：零配置 Demo

默认使用 SQLite，并由 Compose 启动 Redis、后端和前端：

```bash
docker compose up -d --build redis backend frontend
```

访问：

- 前端：<http://127.0.0.1:3000>
- API 文档：<http://127.0.0.1:8000/docs>
- Readiness：<http://127.0.0.1:8000/ready>

### 方案 B：完整 PostgreSQL + Redis

容器内连接 PostgreSQL 时必须使用服务名 `postgres`，不能使用 `localhost`：

```bash
export SOCIALEASE_DATABASE_URL='postgresql+psycopg://socialease:socialease@postgres:5432/socialease'
export LLM_ENABLED=false

docker compose build
docker compose up -d postgres redis
docker compose run --rm backend alembic upgrade head
docker compose up -d backend frontend
docker compose ps
```

健康状态应满足：

```bash
curl http://127.0.0.1:8000/ready
```

其中 `database.provider` 应为 `postgres`，`task_state.backend` 应为 `redis`，三个任务状态探针均应为 `true`。

停止服务但保留容器：

```bash
docker compose stop
```

删除容器和网络但保留数据库 Volume：

```bash
docker compose down
```

不要在需要保留本地数据时执行 `docker compose down -v`。

## 本地开发

启动依赖：

```bash
docker compose up -d postgres redis
```

后端：

```bash
cd backend
pip install -r requirements.txt
export SOCIALEASE_DATABASE_URL='postgresql+psycopg://socialease:socialease@localhost:5432/socialease'
export SOCIALEASE_REDIS_URL='redis://localhost:6379/0'
alembic upgrade head
uvicorn app.main:app --reload
```

前端：

```bash
cd frontend
npm install
npm run dev
```

复制本地配置模板：

```bash
cp .env.example .env
```

`.env`、密钥、个人数据和面试准备材料不应提交到 Git。

验证仓库隐私边界：

```bash
make privacy-check
```

使用一次性测试数据库运行完整 PostgreSQL Adapter、API runtime 和重启持久化回归：

```bash
SOCIALEASE_TEST_DATABASE_URL='postgresql+psycopg://socialease:socialease@localhost:5432/socialease_test' \
make test-postgres-runtime
```

该命令包含 migration downgrade/upgrade，只能指向可丢弃的测试数据库，不能指向开发或生产数据。

## 可选启用 LLM

不配置模型时，系统使用确定性 Fallback，核心工作流仍可运行。启用 OpenAI-compatible Provider 时，在本地 `.env` 中配置：

```env
LLM_ENABLED=true
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=replace-with-your-key
LLM_MODEL=replace-with-model-name

# 可选：启用全局语义 Output Guardrail
OUTPUT_GUARDRAIL_LLM_ENABLED=true
```

完整变量与生产建议见 [环境配置说明](docs/environment_config.md)。

## 测试与评测

后端确定性测试：

```bash
cd backend
pytest -m 'not llm_eval and not redis_integration'
```

确定性 Eval 与 Gate：

```bash
make eval
make eval-gate
```

Redis 集成测试：

```bash
docker compose up -d redis
make test-redis-context
```

Calendar MCP 确定性与真实协议契约测试：

```bash
make test-calendar-mcp
```

独立启动本地 Streamable HTTP MCP Server：

```bash
make dev-calendar-mcp
SOCIALEASE_CALENDAR_MCP_URL=http://127.0.0.1:8010/mcp make dev-backend
```

PostgreSQL 集成测试需要设置独立测试数据库 URL：

```bash
docker compose up -d postgres
docker compose exec postgres createdb -U socialease socialease_test  # 首次运行

cd backend
SOCIALEASE_DATABASE_URL='postgresql+psycopg://socialease:socialease@localhost:5432/socialease_test' \
alembic upgrade head

SOCIALEASE_TEST_DATABASE_URL='postgresql+psycopg://socialease:socialease@localhost:5432/socialease_test' \
pytest tests/test_postgres_*.py
```

前端检查：

```bash
cd frontend
npm run typecheck
npm run lint
npm run build
```

两套 Playwright 用例使用不同认证配置但共享同一 worktree 的 `.next` 目录，请顺序执行
`make test-e2e` 和 `make test-e2e-production-auth`；不要用 `make -j` 并行运行。如需并行，
应使用不同 worktree 或独立的 Next.js 构建目录。

DeepEval 会调用真实 LLM 并可能产生费用，因此默认跳过：

```bash
cd backend
pip install -r requirements-eval.txt
cd ..
make eval-llm
make eval-output-guardrail
```

Memory Vector/Hybrid Benchmark 使用固定的本地中文 ONNX Embedding，不调用外部
LLM API，但首次运行会下载约 90MB 模型，因此也与默认 CI 分离：

```bash
cd backend
pip install -r requirements-vector-eval.txt
cd ..
make eval-memory-vector
```

当前实验固定 `FastEmbed 0.8.0`、`BAAI/bge-small-zh-v1.5` 512 维模型及模型
revision；Vector/Hybrid 在应用用户、Consent、状态、类型、过期和安全硬过滤之后运行，
不能替代权限过滤。具体场景不再是硬过滤枚举：开放场景通过 `scenario_id` /
`practice_thread_id` 保持连续性，通过受控技能标签和语义相关性支持跨场景迁移。

Memory Center 位于前端 `/memory`，明确区分稳定设置、Active Thread、情节记忆、
待确认候选和普通聊天历史。用户可以查看来源与保存原因，安全编辑摘要，使用乐观锁
归档、恢复或物理删除单条记忆，并按记忆类型关闭未来写入和检索。Memory Center
快照会复用同一次用户作用域数据加载生成只读 Doctor 报告；独立 Doctor API 仍可
用于单独刷新。对应 API 位于：

```text
GET    /api/users/{user_id}/memories
PATCH  /api/users/{user_id}/memories/{memory_id}
POST   /api/users/{user_id}/memories/{memory_id}/archive
POST   /api/users/{user_id}/memories/{memory_id}/restore
DELETE /api/users/{user_id}/memories/{memory_id}
GET    /api/users/{user_id}/memory-proposals
POST   /api/users/{user_id}/memory-proposals/{proposal_id}/confirm
POST   /api/users/{user_id}/memory-proposals/{proposal_id}/reject
PUT    /api/users/{user_id}/memory/personalization/{memory_type}
GET    /api/users/{user_id}/memory-doctor
```

Memory Doctor 是用户作用域的只读质量检查：检测重复/冲突记忆、长期未使用项、
授权或类别设置不一致、缺失来源、异常时间、Active Memory 预算、过期练习线程和
久未确认候选。报告只返回稳定问题码、数量及哈希化对象标识，不返回记忆正文，也
不会自动修复。当前生产向量索引未启用，因此孤立 embedding 检查会明确返回
`not_applicable`，而不是误报为通过。

Eval 的设计、指标和局限见 [Benchmark Report](docs/benchmark_report.md) 与 [Human Review Rubric](docs/human_review_rubric.md)。

## 安全与隐私边界

- 不输出诊断结论，不承诺治疗效果，不替代专业支持。
- Crisis 输入优先进入升级流程，不继续普通 Skill 或主动练习。
- 不鼓励用户远离可信任的人或现实支持。
- 资源回答必须来自检索结果；未知时明确返回 `unknown`。
- 长期 Memory 只保存授权后的低敏结构化信息，模型不能自主决定永久记忆。
- Trace 和 Context Diagnostics 不复制敏感字段原值。
- 用户可以导出和删除本人记录；Crisis、任务结束和账号删除会清理相应短期状态。
- 示例数据必须标记为 Demo，不硬编码未经核验的校园电话和联系人。

更多说明见 [数据留存与隐私](docs/data_retention_and_privacy.md) 和 [真实用户试点清单](docs/real_user_pilot_checklist.md)。

## 文档

- [文档索引](docs/README.md)
- [架构图](docs/architecture_diagram.md)
- [Agent Harness 设计](docs/agent_harness_design.md)
- [环境配置](docs/environment_config.md)
- [部署手册](docs/deployment_runbook.md)
- [知识库设计](docs/knowledge_base_design.md)
- [生产化边界](docs/production_readiness.md)
- [监控、备份与告警](docs/monitoring_backup_and_alerting_checklist.md)
- [架构决策记录](docs/adr/)

## 当前边界

当前项目是安全敏感场景下的 Agent 工程原型，不是通用 Agent Framework，也不是医疗产品。现阶段仍以本地/合成评测为主，尚未经过真实用户试点；自建认证、BM25 检索和应用级 Trace 均保留进一步生产化空间。
