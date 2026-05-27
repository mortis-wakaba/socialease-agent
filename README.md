# SocialEase Agent

SocialEase Agent 是一个面向大学生社交压力场景的安全可控 Agent 系统。它不是医疗产品，不做诊断，不替代心理咨询，也不承诺治疗效果。

当前 MVP 范围：

- FastAPI 后端
- `POST /api/chat`
- Hybrid Safety Classifier：deterministic rules 提供不可降级底线，LLM 可上调隐晦风险
- LLM-first Intent Router：启用 LLM 时默认语义路由，失败时回退 rule-based router
- 简单的非医疗化 Support Agent
- Crisis Escalation Flow：危机输入会绕过普通 agent
- Role-play Agent：支持社交场景模拟、接入 Social Skills RAG、session 保存、LLM-backed next turn 和结构化反馈
- CBT Worksheet Agent：支持 validated LLM extraction + rule-based fallback，将输入整理为非医疗化自助反思 worksheet
- Exposure Planner：生成由易到难的社交练习计划，接入 Social Skills RAG，并根据反馈调整下一任务
- 分层知识库 RAG MVP：Social Skills、Support Resources、Safety Policy、Product Rubrics、Campus Resources Demo，基于本地 markdown 检索并返回带来源类型的引用
- SQLite 持久化 + repository 层
- LLM provider abstraction：支持 OpenAI-compatible provider（例如 DeepSeek）
- Agent Harness：统一编排 safety、routing、skill dispatch、memory/persistence 和 trace
- Skill Registry：将 crisis、support、role-play、worksheet、exposure、support RAG 等能力登记为 skills
- Trace Logger + `llm_usage` 元数据
- Agent Eval Suite：覆盖 safety、safety red-team、routing、RAG citation、skill-level rubric、E2E workflow 和 fallback
- `GET /api/runs/{run_id}` 用于查看单次 agent run trace
- 使用 pytest 覆盖 safety、routing、RAG、LLM fallback、skills registry 和 API workflow

## 快速查看

- Demo walkthrough：[`docs/demo_walkthrough.md`](docs/demo_walkthrough.md)
- Agent Harness 设计：[`docs/agent_harness_design.md`](docs/agent_harness_design.md)
- Production readiness gap analysis：[`docs/production_readiness.md`](docs/production_readiness.md)
- 面试 Q&A：[`docs/interview_qa.md`](docs/interview_qa.md)
- 架构决策记录：[`docs/adr/`](docs/adr/)
- 项目讲解与简历包装：[`docs/project_pitch.md`](docs/project_pitch.md)
- 知识库分层设计：[`docs/knowledge_base_design.md`](docs/knowledge_base_design.md)

## 一键 Docker 启动

```bash
docker compose up --build
```

启动后访问：

- 前端：<http://127.0.0.1:3000>
- 后端 API docs：<http://127.0.0.1:8000/docs>
- 后端健康检查：<http://127.0.0.1:8000/health>

不配置 LLM API key 时，系统会使用 deterministic fallback 跑通完整 demo。

## 系统架构

```text
User Input
  → Agent Harness
  → Safety Classifier
  → Intent Router
  → Skill Registry / Skill Dispatch
  → Knowledge RAG / User Memory / SQLite Persistence
  → Trace Logger
  → Frontend UI
```

```text
┌────────────┐
│  Frontend  │
│ Next.js UI │
└─────┬──────┘
      │ REST API
┌─────▼──────────────────────────────────────────────┐
│                    FastAPI Backend                  │
│                                                     │
│  ┌────────┐   ┌────────┐   ┌────────────────────┐  │
│  │ Safety │ → │ Router │ → │ Skill Registry     │  │
│  └────────┘   └────────┘   └─────────┬──────────┘  │
│      │             │                  │             │
│      │             │          ┌───────▼────────┐    │
│      │             │          │ Skills + RAG   │    │
│      │             │          └───────┬────────┘    │
│      │             │                  │             │
│  ┌───▼─────────────▼──────────────────▼─────────┐  │
│  │ Trace Logger / Repository / SQLite Persistence│  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

## 推荐目录结构

```text
backend/
  app/
    api/
    agents/
    db/
    knowledge/
    llm/
    skills/
    workflow/
    safety/
    memory/
    tracing/
    evals/
  data/
  tests/
frontend/
  app/
```

## 启动后端

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API 文档地址：

```text
http://127.0.0.1:8000/docs
```

## 启动前端

前端使用 Next.js + React + TypeScript + Tailwind。先启动后端，再启动前端：

```bash
cd frontend
npm install
npm run dev
```

前端地址：

```text
http://127.0.0.1:3000
```

默认 API 地址是：

```text
http://127.0.0.1:8000
```

如果后端运行在其他地址，可以创建 `frontend/.env.local`：

```text
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

前端页面：

- `/chat`：主聊天界面，展示 risk level、intent 和 run_id；
- `/practice`：Role-play 场景选择、对话和 feedback；
- `/worksheet`：CBT 风格自助反思 worksheet 和 disclaimer；
- `/support`：真实公开支持资源查询，展示 grounded / unknown / blocked 与 citations；
- `/progress`：社交练习阶梯和 completed / skipped / too_hard 操作；
- `/trace`：输入 run_id 查看 Safety → Router → Agent → Memory → Output。

示例请求：

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo_user","message":"我想模拟课堂发言，怕自己说不清楚","context":{}}'
```

## 运行测试

```bash
cd backend
pytest
```

当前测试覆盖：

- Safety Classifier：至少 10 个 rule-based 分类用例；
- Intent Router：至少 10 个 keyword scoring 路由用例；
- API workflow：验证 `/api/chat`、crisis escalation、trace 查询和 trace 核心字段。
- Skill Registry：验证 harness 可解析 crisis/support executable skills，并暴露 role-play、worksheet、exposure、support RAG 等 skill metadata。
- Harness API：验证 `/api/harness/capabilities` 能公开 runtime loop、permissions、skills、knowledge layers 和 observation，并验证 `/api/harness/metrics` 的轻量聚合指标。
- Role-play API：验证创建 session、发送 message、获取 feedback、crisis message 拦截。
- CBT Worksheet API：验证完整输入、信息不足输入、crisis 输入拦截和非医疗化 disclaimer。
- Exposure API：验证计划生成、completed / skipped / too_hard 的难度调整和用户进度查询。
- Knowledge RAG API：验证 social_skills / safety_policy 检索、unknown query、citations 和不生成假联系方式。

## Agent Harness / Skills / Eval 对应关系

本项目将现有 agent workflow 组织为三个工程概念：

- **Harness**：`backend/app/workflow/engine.py` 中的 `AgentHarness`，负责一次请求的完整生命周期，包括 safety、permission gate、routing、skill dispatch、hooks、trace 和 fallback。
- **Skills**：`backend/app/skills/` 中的 skill interface、registry、manifest loader 和 `SKILL.md` manifests。当前 chat harness 可执行 `crisis_escalation_skill` 和 `general_support_skill`，同时 registry 暴露 role-play、worksheet、exposure planning、support resource RAG 等专业 skill metadata，对应已有 API/agent 模块。
- **Permissions**：`backend/app/safety/permissions.py` 将 safety classification 转换为 harness-level `ALLOW` / `ESCALATE` 决策；crisis 会跳过普通 routing 和 skills。
- **Hooks**：`backend/app/workflow/hooks.py` 提供 before/after safety、routing、skill、trace 的扩展点，方便后续 audit logging、metrics 或 eval capture。
- **Eval Suite**：`backend/app/evals/` 中的 JSONL cases、metrics 和 runner，用来固定 safety、safety red-team、routing、citation grounding、roleplay feedback、worksheet extraction 和 E2E harness workflow 等关键行为。

这层抽象的目标不是制造复杂插件系统，而是让项目表达更接近现代 agent runtime：能力可登记、调度可解释、风险可评测。

## 核心 API

### Harness API

#### `GET /api/harness/capabilities`

返回当前 Agent Harness 的能力发现信息，适合 demo、调试和面试展示。

```bash
curl http://127.0.0.1:8000/api/harness/capabilities
```

响应包含：

- `runtime_loop`：AgentHarness 的主循环节点；
- `permission_actions`：当前支持的 permission decision，例如 `allow`、`escalate`；
- `skills`：已注册 skills、支持 intents、安全边界和 manifest 状态；
- `knowledge_layers`：可用知识库层；
- `observation`：trace、eval、`llm_usage` 等可观察性能力。

#### `GET /api/harness/metrics`

返回最近 harness runs 的轻量聚合指标。

```bash
curl "http://127.0.0.1:8000/api/harness/metrics?limit=100"
```

响应包含：

- `total_runs`；
- `crisis_runs`；
- `fallback_runs`；
- `average_latency_ms`；
- `intent_counts`；
- `selected_agent_counts`。

该接口用于 demo observability，不替代生产级监控系统。

### `POST /api/chat`

请求：

```json
{
  "user_id": "demo_user",
  "message": "我想模拟课堂发言，怕自己说不清楚",
  "context": {}
}
```

响应包含：

- `run_id`
- `risk_level`
- `intent`
- `response`
- `structured_data`
- `trace`

Trace 中会记录：

- `safety_result`
- `intent_result`
- `selected_agent`
- `latency_ms`

### Role-play API

支持场景：

- `classroom_speech`
- `group_discussion`
- `dorm_conflict`
- `club_icebreaking`
- `invite_classmate_meal`
- `ask_teacher_question`
- `interview_self_intro`
- `refuse_request`
- `express_disagreement`

创建 session：

```bash
curl -X POST http://127.0.0.1:8000/api/roleplay/start \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo_user","scenario":"classroom_speech","difficulty":2}'
```

开始 session 时，系统会根据 `scenario` 查询 `social_skills` 知识库，并把结果保存到 `retrieved_guidance`。如果没有检索到相关 demo 文档，会 fallback 到通用安全练习脚手架，并标注：

```json
{
  "no_guidance_found": true
}
```

发送一轮消息：

```bash
curl -X POST http://127.0.0.1:8000/api/roleplay/message \
  -H "Content-Type: application/json" \
  -d '{"session_id":"替换为上一步返回的 session_id","user_id":"demo_user","message":"我想先说我的核心观点。"}'
```

获取反馈：

```bash
curl -X POST http://127.0.0.1:8000/api/roleplay/feedback \
  -H "Content-Type: application/json" \
  -d '{"session_id":"替换为上一步返回的 session_id","user_id":"demo_user"}'
```

Role-play session 会保存：

- `session_id`
- `user_id`
- `scenario`
- `difficulty`
- `messages`
- `retrieved_guidance`
- `created_at`
- `updated_at`

Feedback 会返回：

- `clarity_score`
- `naturalness_score`
- `assertiveness_score`
- `empathy_score`
- `strengths`
- `suggestions`
- `next_try_prompt`
- `citations`

安全约束：

- 用户每一轮输入都会先经过 Safety Classifier；
- crisis 输入会中断 role-play，进入 crisis escalation；
- role-play 输出不得包含诊断或治疗承诺；
- feedback citations 来自 Social Skills 项目自写练习文档。

### CBT Worksheet API

这只是 CBT 风格的自助反思练习，仅用于整理想法和下一步行动；它不用于判断疾病，也不能替代专业心理支持。

创建 worksheet：

```bash
curl -X POST http://127.0.0.1:8000/api/worksheet/create \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo_user","message":"情境：明天课堂发言。自动想法：我肯定会说错被大家笑。情绪：焦虑。强度：7/10。支持证据：之前发言卡过壳。反对证据：上次小组讨论同学认真听我说完。替代想法：我可能会紧张，但可以先说核心观点。下一步：今晚练习开场两遍。"}'
```

获取 worksheet：

```bash
curl http://127.0.0.1:8000/api/worksheet/替换为返回的_worksheet_id
```

Worksheet record 会保存：

- `worksheet_id`
- `user_id`
- `source_message`
- `fields`
- `citations`
- `disclaimer`
- `missing_fields`
- `gentle_followup_questions`
- `created_at`

`fields` 包含：

- `situation`
- `automatic_thought`
- `emotion`
- `emotion_intensity`
- `evidence_for`
- `evidence_against`
- `alternative_thought`
- `next_action`

如果输入信息不足，响应会返回：

- `missing_fields`
- `gentle_followup_questions`

创建 worksheet 时会查询 `social_skills` 知识库中的 CBT 反思指南，并把引用保存到 `citations`。这些引用来自本地 markdown 文档，例如 `cbt_reflection_guide.md`。

如果输入包含 crisis 风险表达，系统会暂停自助练习，不创建普通 worksheet，并建议联系可信任的人、学校心理中心或当地紧急服务。

### Exposure Planner API

这是社交练习的分级计划，仅用于安排安全、可控、可停止的小步骤；它不用于判断疾病，也不能替代专业心理支持。

创建练习阶梯：

```bash
curl -X POST http://127.0.0.1:8000/api/exposure/plan \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo_user","target_scenario":"课堂发言","current_anxiety_level":7,"previous_attempts":["写过开场白"]}'
```

返回的 `tasks` 包含 5-7 个由易到难的任务。当前 MVP 默认生成 6 个任务，每个任务包含：

- `task_id`
- `title`
- `description`
- `difficulty`
- `estimated_time_minutes`
- `success_criteria`
- `fallback_task`
- `citations`

创建计划时会查询 `social_skills` 知识库中的 `exposure_training_guide.md` 相关内容，并把引用写入每个任务的 `citations`。

提交任务反馈：

```bash
curl -X POST http://127.0.0.1:8000/api/exposure/complete \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo_user","task_id":"替换为任务 task_id","status":"completed","anxiety_before":7,"anxiety_after":4,"reflection":"完成后发现比想象中可控。"}'
```

`status` 支持：

- `completed`
- `skipped`
- `too_hard`

调整规则：

- `too_hard`：降低下一任务难度；
- `completed` 且 `anxiety_after` 下降：略微提高下一任务难度；
- `skipped`：给更小任务。

安全规则：

- 创建计划前会先经过 Safety Classifier；
- crisis 输入不会生成计划，响应中 `blocked=true` 且 `plan=null`；
- 所有文案保持非医疗化，不承诺练习效果。

查看用户当前 exposure 计划：

```bash
curl http://127.0.0.1:8000/api/users/demo_user/exposure
```

### Knowledge RAG API

当前 MVP 是本地 markdown + keyword retriever，不调用 LLM。知识库分为：

- `social_skills`：用于社交情境训练、CBT 自助反思、暴露练习计划。
- `support_resources`：用于真实、公开、可验证的支持资源查询。
- `safety_policy`：用于安全边界、危机响应、非医疗化表达规范。
- `product_rubrics`：用于内部反馈、抽取和评测规则。
- `campus_resources_demo`：仅用于演示未来校园资源导入后的数据形态。

知识库目录：

```text
backend/data/knowledge_base/social_skills/
backend/data/knowledge_base/support_resources/
backend/data/knowledge_base/safety_policy/
backend/data/knowledge_base/product_rubrics/
backend/data/knowledge_base/campus_resources_demo/
```

每篇文档都包含 frontmatter：

```text
---
title: ...
source_name: ...
source_type: external_public | project_authored | demo
source_url: ...
doc_type: guide | policy | rubric | scenario
kb_type: ...
audience: user_facing | internal_only
review_status: reviewed
last_reviewed: 2026-05-18
---
```

查询知识库：

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge/query \
  -H "Content-Type: application/json" \
  -d '{"query":"课堂发言怎么准备核心观点","kb_type":"social_skills"}'
```

查询安全策略：

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge/query \
  -H "Content-Type: application/json" \
  -d '{"query":"crisis 自伤 自杀 响应 怎么处理","kb_type":"safety_policy"}'
```

查询公开支持资源：

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge/query \
  -H "Content-Type: application/json" \
  -d '{"query":"social anxiety CBT self-help public resource","kb_type":"support_resources"}'
```

查询内部 rubric：

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge/query \
  -H "Content-Type: application/json" \
  -d '{"query":"clarity naturalness assertiveness empathy rubric","kb_type":"product_rubrics"}'
```

响应包含：

- `answer`
- `citations`
- `unknown`
- `confidence`

约束：

- citations 必须来自实际 markdown 文档，并包含 `source_name`、`source_type`、`source_url`；
- 检索不到时 `unknown=true`；
- 不编造知识库中不存在的资源、电话、热线或学校信息；
- 当前返回的是检索摘要，不是 LLM 生成回答。

### Support Resource API

#### `POST /api/support/query`

用于查询真实、公开、可验证的支持资源。这个接口默认只查询 `support_resources`，而不是把内部 rubric 或 demo 校园资源混进普通资源导航。

示例：

```bash
curl -X POST http://127.0.0.1:8000/api/support/query \
  -H "Content-Type: application/json" \
  -d '{"query":"social anxiety CBT self-help public resource"}'
```

响应包含：

- `answer`
- `citations`
- `unknown`
- `confidence`
- `safety_result`
- `blocked`

安全规则：

- 普通查询只返回已收录的真实公开资源；
- 查询不到时 `unknown=true`；
- crisis 输入会先经过 Safety Classifier，返回 `blocked=true`，并暂停普通资源检索；
- 不会把 `campus_resources_demo` 冒充为真实支持资源。

## 安全边界

- 不做诊断。
- 不承诺治疗效果。
- 不鼓励用户远离现实支持。
- 遇到自伤、自杀、伤害他人或严重危机表达时，必须进入 crisis escalation。
- Crisis response 应建议用户联系可信任的人、学校心理中心或当地紧急服务。

## 当前进度

已完成：

- **阶段 1：数据持久化**
  - 增加 SQLite 持久化；
  - 新增 `backend/app/db/` repository 层；
  - 将 trace、role-play、worksheet、exposure store 从纯内存实现切换为可替换 repository；
  - 保留 in-memory repository，便于测试替换。
- **阶段 2：User Memory**
  - 新增轻量 profile API；
  - 从已有练习记录聚合近期场景、练习次数、最近 anxiety level 和偏好难度；
  - 明确 memory 只保存 demo summary，不保存诊断标签，也不额外复制危机原文；
  - 增加 profile API 测试，覆盖普通练习更新与 crisis 不进入普通 summarization。
- **阶段 3：Agent Eval Suite**
  - 新增 JSONL eval dataset、loader、metric 和 `python -m app.evals.run`；
  - 覆盖 safety、intent、RAG citation、roleplay feedback、worksheet extraction；
  - 增加稳定回归测试和 crisis hard requirement。
- **阶段 4：Support Resource RAG**
  - 新增真实公开资源层 `support_resources`；
  - 新增 `POST /api/support/query` 与前端 Support 页面；
  - citation 区分 external public / project authored / demo；
  - crisis 查询优先安全升级，demo 校园资源不冒充真实服务；
  - 项目级规则集中记录于 `safety_policy/project_authored/support_resource_policy.md`。
- **阶段 5：LLM 节点接入**
  - 新增 `backend/app/llm/` provider abstraction 与 DeepSeek 风格配置示例；
  - Safety 升级为 hybrid design，Intent Router 在启用 LLM 时默认走语义路由；
  - role-play next turn 与 worksheet extraction 支持 LLM-first + deterministic fallback；
  - `llm_usage` 覆盖 safety、routing、role-play、worksheet 的使用与 fallback 状态。
- **阶段 6：前端质量提升**
  - 增加统一 API 错误解析、空状态和表单校验；
  - `/practice` 支持 session reset、citations 展开和 LLM usage 展示；
  - `/progress` 增加 attempt history；
  - `/trace` 增加 Safety → Router → Agent → Memory → Output 流程节点与 LLM usage；
  - `/support`、`/worksheet`、`/chat` 展示更清楚的状态与安全/LLM 元数据。
- **阶段 7：Docker Compose 与部署**
  - 新增后端和前端 Dockerfile；
  - 新增 `docker-compose.yml`，一条命令启动前后端；
  - SQLite 数据库使用 Docker named volume 持久化；
  - 后端与前端均配置 healthcheck；
  - demo knowledge base 会随后端镜像一起打包。
- **阶段 8：展示材料和简历包装**
  - 新增 demo walkthrough，覆盖普通社交压力、role-play feedback、worksheet、support RAG、crisis escalation 和 trace；
  - 新增项目 pitch / 简历包装文档，包含架构图、技术亮点、面试 tradeoffs 和中英文简历 bullet points；
  - 新增 production readiness gap analysis、interview Q&A 和 ADR，沉淀架构权衡；
  - 新增 `/api/harness/capabilities` 能力发现接口和 `/api/harness/metrics` 轻量可观察性接口；
  - README 增加快速查看、一键 Docker 启动和系统架构入口。

后续可选增强：

1. 部署到公网云平台，提供可直接访问的 demo link；
2. 补充 screenshots 或 1-2 分钟演示视频；
3. 增加前端 Vitest / Testing Library 组件测试。

知识库分层设计见 [`docs/knowledge_base_design.md`](docs/knowledge_base_design.md)：

- `social_skills`：外部社交焦虑 / CBT / exposure 依据 + 项目自写练习内容；
- `support_resources`：真实、公开、可验证的支持资源；
- `safety_policy`：外部边界依据 + 项目安全策略；
- `product_rubrics`：roleplay、worksheet、exposure、eval 的内部规则；
- demo 校园资源与真实支持资源严格区分。

### User Memory API

#### `GET /api/users/{user_id}/profile`

返回轻量 demo 用户状态：

- 近期练习场景；
- role-play、worksheet、exposure attempt 次数；
- 最近 anxiety level；
- 基于最近 role-play 的偏好难度。

Memory 数据边界：

- 只从已有练习记录聚合轻量状态；
- 不保存诊断标签；
- 不额外复制危机原文；
- 返回值明确标注为 demo summary；
- 删除接口目前仅预留，不承诺已实现。

验证方式：

- 新增 profile API 测试；
- 完成一次 roleplay / exposure 后，profile 统计更新；
- crisis flow 不进入普通 memory summarization。

### 阶段 3：Evaluation 模块

目标：让项目可评测，而不是只靠人工试用。

交付物：

- `backend/app/evals/` 小型测试集；
- safety classification cases；
- intent routing cases；
- RAG citation cases；
- roleplay feedback cases；
- worksheet extraction cases；
- 一个可运行评测脚本。

执行计划：

- 用 JSONL 或 YAML 保存 eval cases；
- 编写 `python -m app.evals.run`；
- 输出 accuracy、blocked crisis rate、citation hit rate、unknown precision；
- 把当前 pytest 中的代表性 case 抽一部分进入 eval dataset；
- README 增加评测命令和指标解释。

验证方式：

- `pytest` 覆盖 eval loader 和 metric；
- eval 脚本能输出稳定结果；
- safety crisis case 必须 100% 进入 crisis。

当前实现：

- `backend/app/evals/data/` 使用 JSONL 保存小型 demo case；
- 覆盖 safety、intent、RAG citation、roleplay feedback、worksheet extraction；
- `python -m app.evals.run` 输出：
  - `safety_accuracy`
  - `blocked_crisis_rate`
  - `intent_accuracy`
  - `citation_hit_rate`
  - `unknown_precision`
  - `roleplay_feedback_pass_rate`
  - `worksheet_extraction_pass_rate`

运行评测：

```bash
cd backend
python -m app.evals.run
```

### 阶段 4：Support Resource RAG

目标：提供真实、可验证的支持资源查询能力，而不是为了演示而伪造某个学校的资源库。

设计原则：

- 当前版本优先接入公开、可核验的支持资源内容；
- 不预置虚构学校电话、地址、部门或联系人；
- citation 必须指向实际来源文档；
- 查不到时明确返回 unknown，而不是补全看似可信的信息；
- 未来如果部署到具体学校，再通过经过审核的资源导入流程扩展为 campus-specific resource RAG。

交付物：

- `backend/data/knowledge_base/support_resources/`；
- 来自公开来源、可验证的 markdown 文档；
- 与 `social_skills`、`safety_policy`、`product_rubrics` 分层协作的知识库结构；
- `POST /api/support/query` 或扩展现有 `/api/knowledge/query`；
- citation 中区分项目内部安全策略与外部公开支持资源；
- 前端支持资源查询入口；
- 为未来 `campus_resources/` 导入能力预留结构。

执行计划：

- 优先整理适合长期保留、来源清晰的公开资源内容；
- 每篇文档保留 frontmatter，写明标题、来源和文档类型；
- 外部权威来源优先采用 NIMH、NHS / NHS Inform、CCI、WHO、American Psychiatric Association / APA；
- 项目自有内容单独维护大学生社交场景脚本、roleplay 反馈标准、练习任务阶梯和安全策略；
- RAG 响应必须带 citations；
- unknown 时明确说明当前资源库没有足够信息；
- crisis 查询仍优先走 Safety Policy / escalation，而不是普通资源检索；
- 不把“公共支持资源”伪装成“某所学校已经提供的正式服务”。

验证方式：

- support resource RAG 能检索真实公开资源；
- unknown query 返回 `unknown=true`；
- 测试确认不会生成未经来源支持的联系方式；
- citation 能区分 internal policy 与 external public resource；
- 前端展示 citations；
- 后续接入具体学校资源时，不需要推翻当前检索结构。

当前实现：

- `backend/data/knowledge_base/support_resources/` 已收录真实公开资源整理稿；
- `backend/data/knowledge_base/campus_resources_demo/` 仅保存 demo 资源样例；
- `backend/data/knowledge_base/safety_policy/project_authored/support_resource_policy.md` 记录阶段 4 的项目级检索规则；
- `POST /api/support/query` 只查询真实公开资源；
- `/support` 页面展示 grounded / unknown / blocked 状态和 citations。

### 阶段 5：LLM 节点接入

目标：在保留确定性安全底线的前提下，让普通语义理解、生成和抽取默认优先使用 LLM，并在失败时可靠回退。

交付物：

- LLM client 抽象；
- 可配置的 provider / model；
- LLM roleplay response；
- LLM worksheet extractor；
- LLM-first intent router；
- hybrid safety classifier；
- prompt 和 safety policy 文档。

执行计划：

- 在 `BaseSafetyClassifier`、`BaseIntentRouter` 基础上增加 LLM implementation；
- Safety 使用 conservative hybrid design：deterministic rules 提供不可降级底线，LLM 只允许上调风险；
- 启用 LLM 时，intent routing 默认优先使用语义路由；
- 对 roleplay / worksheet 使用 RAG citations 作为 grounded context；
- 输出前做安全后处理，禁止诊断和效果承诺；
- 增加 `.env.example`，不提交真实 API key。

验证方式：

- 无 API key 时系统仍可用 rule-based MVP；
- LLM 输出不得绕过 crisis flow；
- eval 保留 deterministic baseline，LLM 节点另有单元测试覆盖成功、非法输出和 fallback；
- 手动测试 5 个典型场景。

当前实现：

- `backend/app/llm/` 新增 provider-agnostic 抽象；
- `BaseLLMClient` 只暴露最小 `generate_text(...)` 契约；
- `OpenAICompatibleLLMClient` 可用于 DeepSeek 等 OpenAI-compatible provider；
- `LLMConfig` / `create_llm_client` 从环境变量创建可选 client；
- 默认 `LLM_ENABLED=false`，未配置 key 时系统继续走 deterministic MVP；
- `backend/.env.example` 提供 DeepSeek 风格配置示例；
- `backend/app/llm/prompts.py` 集中保存 safety、routing、role-play、worksheet prompt。

当前已接入节点：

- `LlmIntentRouter`
  - 启用 LLM 时默认优先使用语义路由，而不是只在关键词无命中时补救；
  - crisis 仍由 Safety Classifier 先行决定，router 不可覆盖；
  - LLM JSON 非法、输出了不允许的 intent 或 provider 调用失败时，自动回退到 rule-based router。
- `RoleplayAgent.next_turn`
  - 有可用 LLM client 时，使用 scenario、difficulty、retrieved guidance 和最近对话生成下一轮；
  - 无 client 或 provider 调用失败时，自动回退到原有 deterministic response；
  - crisis 输入仍在 API 层先被拦截，不进入普通 LLM generation。
- `WorksheetAgent.create_fields`
  - 有可用 LLM client 时，要求模型只返回严格 JSON；
  - 只抽取用户已明确提供的信息，缺失字段必须为 `null`；
  - JSON 非法、字段异常或 provider 调用失败时，自动回退到现有 rule-based extractor；
  - crisis 输入仍在 API 层先被拦截，不创建普通 worksheet。

可观察性：

- `safety_result.llm_usage` 记录 hybrid safety 是否使用了 LLM 或 fallback；
- chat trace 中的 `intent_result.llm_usage` 记录 intent router 是否使用了 LLM 或 fallback；
- role-play message response 和 worksheet create response 都返回 `llm_usage`；
- `llm_usage.used=true` 表示本次成功使用 LLM；
- `llm_usage.fallback_used=true` 表示尝试过 LLM，但因 provider 或输出问题回退到 deterministic 逻辑；
- 未启用 LLM 时相关字段都为 `false`。

DeepSeek 示例配置：

```env
LLM_ENABLED=true
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=替换为你的_key
LLM_MODEL=deepseek-chat
LLM_TIMEOUT_SECONDS=30
```

设计约束：

- 业务 agent 只依赖 `BaseLLMClient`，不直接依赖某一家 provider；
- DeepSeek 通过 OpenAI-compatible adapter 接入，而不是写死到业务层；
- Safety 使用 conservative hybrid design：
  - deterministic rules 提供不可被降级的安全底线；
  - LLM 可对更隐晦的语义风险进行上调；
  - 最终取更高风险等级；
- 后续 LLM 节点必须保留 deterministic fallback。

手动验证：

1. 保持 `LLM_ENABLED=false`，调用 chat / role-play / worksheet，确认相关 `llm_usage` 都是 `used=false, fallback_used=false`；
2. 配置 DeepSeek key 后启用 `LLM_ENABLED=true`，再次调用 chat / role-play / worksheet，确认正常请求会返回 `used=true`；
3. 临时填入无效 key 或不可达 `LLM_BASE_URL`，确认接口仍返回成功结果，但对应 `fallback_used=true`；
4. 对 chat / role-play / worksheet 发送 crisis 输入，确认仍走安全升级且不进入普通 LLM generation；
5. 对较隐晦但有风险的表达做手动检查，确认 hybrid safety 可以在 rule-based 未命中时上调风险等级。

### 阶段 6：前端质量提升

目标：让前端从“能用”变成“适合展示和答辩演示”。

交付物：

- 更完整的 loading、empty、error 状态；
- 表单校验；
- 前端测试；
- 更清楚的 trace 可视化；
- 移动端布局细化。

执行计划：

- 增加 Vitest / React Testing Library；
- 给 API client 增加统一错误解析；
- `/practice` 增加 session reset 和 citations 展开；
- `/progress` 增加 attempt history；
- `/trace` 增加 Safety → Router → Agent → Memory → Output 的流程节点；
- 检查所有页面的非医疗化文案。

验证方式：

- `npm run typecheck`；
- `npm run build`；
- 前端组件测试通过；
- 手动走通 chat、practice、worksheet、progress、trace 五个页面。

当前实现：

- API client 增加统一错误解析，能展示 FastAPI validation detail；
- 共享 UI 增加 `EmptyState`、`FormHint`、`LLMUsageBadge`；
- `/chat` 展示 safety / router reason 和 LLM usage；
- `/practice` 增加 session reset、citations 展开、非医疗化提示和 turn-level LLM usage；
- `/worksheet` 增加空状态、输入校验、safety / extraction LLM usage；
- `/support` 增加 safety LLM usage 和更明确的 grounded / unknown / blocked 状态；
- `/progress` 增加创建 / 反馈校验和 attempt history；
- `/trace` 增加 Safety → Router → Agent → Memory → Output 可视化，并显示 safety / router LLM usage；
- 当前未新增 Vitest / Testing Library 依赖；前端验证以 `npm run typecheck` 和 `npm run build` 为准。

### 阶段 7：Docker Compose 与部署

状态：已完成基础 demo 部署编排。

目标：让项目可以一键启动，方便演示、提交和部署。

已交付：

- `Dockerfile.backend`：构建 FastAPI 后端镜像，打包 `backend/app` 和 `backend/data`；
- `Dockerfile.frontend`：构建 Next.js 前端镜像；
- `docker-compose.yml`：同时启动 backend 与 frontend；
- `socialease-data` named volume：持久化 SQLite 数据库到容器内 `/data/socialease.db`；
- backend / frontend healthcheck；
- README 一键启动说明。

一键启动：

```bash
docker compose up --build
```

启动后访问：

- 前端：<http://127.0.0.1:3000>
- 后端 API docs：<http://127.0.0.1:8000/docs>
- 后端健康检查：<http://127.0.0.1:8000/health>

停止服务：

```bash
docker compose down
```

如果需要清空 demo SQLite 数据：

```bash
docker compose down -v
```

可选启用 DeepSeek / OpenAI-compatible LLM：

```bash
LLM_ENABLED=true LLM_API_KEY=你的_api_key docker compose up --build
```

也可以在项目根目录创建本地 `.env` 文件，Docker Compose 会自动读取；`.env` 不应提交到 Git。示例：

```env
LLM_ENABLED=true
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=你的_api_key
LLM_MODEL=deepseek-chat
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

部署到非本机环境时，需要把 `NEXT_PUBLIC_API_BASE_URL` 改成浏览器可访问的后端地址。注意这个变量会在前端 build 阶段写入 Next.js 静态包，因此变更后需要重新 build 前端镜像。

验证方式：

- `docker compose config` 检查 Compose 配置；
- `docker compose up --build` 启动前后端；
- 浏览器访问前端、`/docs` 和 `/health`；
- pytest 可在本地稳定运行。

### 阶段 8：展示材料和简历包装

状态：已完成基础展示材料。

目标：把工程能力和安全设计讲清楚，方便大创展示和简历呈现。

已交付：

- README 顶部增加快速查看、一键 Docker 启动和系统架构；
- [`docs/demo_walkthrough.md`](docs/demo_walkthrough.md)：5 分钟 demo script；
- [`docs/agent_harness_design.md`](docs/agent_harness_design.md)：Model + Harness、skills、permissions、hooks、observation 和 eval 对应关系；
- [`docs/production_readiness.md`](docs/production_readiness.md)：上线前缺口、隐私、安全、监控和部署成熟度说明；
- [`docs/interview_qa.md`](docs/interview_qa.md)：面试高频问题和回答思路；
- [`docs/adr/`](docs/adr/)：hybrid safety、skill registry、资源边界、LLM adapter 等架构决策；
- [`docs/project_pitch.md`](docs/project_pitch.md)：项目讲解、架构图、技术亮点、面试 tradeoffs 和中英文简历 bullet points；
- [`docs/knowledge_base_design.md`](docs/knowledge_base_design.md)：知识库分层设计。

Demo walkthrough 覆盖：

- 普通社交压力输入；
- role-play + structured feedback；
- CBT 风格 worksheet；
- support resource RAG；
- crisis escalation；
- trace 与 evaluation。

可选后续增强：

- 补充实际运行截图；
- 录制 1-2 分钟演示视频；
- 如果需要公开体验，再部署到 Render / Railway / Fly.io / VPS 等平台。

验证方式：

- 按 demo script 可以 5 分钟内完整演示；
- README 能让新用户独立启动项目；
- 简历描述能体现 full-stack、agent workflow、RAG、安全和测试。
