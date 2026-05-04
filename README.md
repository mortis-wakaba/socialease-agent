# SocialEase Agent

SocialEase Agent 是一个面向大学生社交压力场景的安全可控 Agent 系统。它不是医疗产品，不做诊断，不替代心理咨询，也不承诺治疗效果。

当前 MVP 范围：

- FastAPI 后端
- `POST /api/chat`
- Rule-based Safety Classifier，并预留 LLM classifier 接口
- Rule-based + keyword scoring Intent Router，并预留 LLM router 接口
- 简单的非医疗化 Support Agent
- Crisis Escalation Flow：危机输入会绕过普通 agent
- Role-play Agent：支持社交场景模拟、接入 Social Skills RAG、session 保存和结构化反馈
- CBT Worksheet Agent：将输入整理为非医疗化自助反思 worksheet
- Exposure Planner：生成由易到难的社交练习计划，接入 Social Skills RAG，并根据反馈调整下一任务
- 双知识库 RAG MVP：Social Skills RAG 和 Safety Policy RAG，基于本地 demo markdown 检索并返回引用
- 内存版 Trace Logger
- `GET /api/runs/{run_id}` 用于查看单次 agent run trace
- 使用 pytest 覆盖 safety、routing 和 API workflow

## 推荐目录结构

```text
backend/
  app/
    api/
    agents/
    workflow/
    safety/
    memory/
    tracing/
    evals/
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
- `/progress`：社交练习阶梯和 completed / skipped / too_hard 操作；
- `/trace`：输入 run_id 查看 Safety → Router → Agent → Output。

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
- Role-play API：验证创建 session、发送 message、获取 feedback、crisis message 拦截。
- CBT Worksheet API：验证完整输入、信息不足输入、crisis 输入拦截和非医疗化 disclaimer。
- Exposure API：验证计划生成、completed / skipped / too_hard 的难度调整和用户进度查询。
- Knowledge RAG API：验证 social_skills / safety_policy 检索、unknown query、citations 和不生成假联系方式。

## 核心 API

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
- feedback citations 来自 Social Skills demo markdown。

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

创建 worksheet 时会查询 `social_skills` 知识库中的 CBT 反思指南，并把引用保存到 `citations`。这些引用来自本地 demo markdown，例如 `cbt_reflection_guide.md`。

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
- `safety_policy`：用于安全边界、危机响应、非医疗化表达规范。

知识库目录：

```text
backend/data/knowledge_base/social_skills/
backend/data/knowledge_base/safety_policy/
```

每篇 demo 文档都包含 frontmatter：

```text
---
title: ...
source: Synthetic demo knowledge base
type: demo
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

响应包含：

- `answer`
- `citations`
- `unknown`
- `confidence`

约束：

- citations 必须来自实际 markdown 文档；
- 检索不到时 `unknown=true`；
- 不编造知识库中不存在的资源、电话、热线或学校信息；
- 当前返回的是检索摘要，不是 LLM 生成回答。

## 安全边界

- 不做诊断。
- 不承诺治疗效果。
- 不鼓励用户远离现实支持。
- 遇到自伤、自杀、伤害他人或严重危机表达时，必须进入 crisis escalation。
- Crisis response 应建议用户联系可信任的人、学校心理中心或当地紧急服务。

## TODO

### 阶段 1：数据持久化

目标：把当前内存存储替换成可恢复、可查询的本地数据库，为后续 User Memory 和展示数据打基础。

交付物：

- SQLite 开发环境；
- 后续可迁移到 PostgreSQL 的 repository 层；
- `runs`、`roleplay_sessions`、`worksheets`、`exposure_plans`、`exposure_attempts` 表；
- 保留当前 API 响应结构不变。

执行计划：

- 选择轻量 ORM 或 SQL toolkit，例如 SQLAlchemy；
- 新增 `backend/app/db/`，包含 engine、session、models、repositories；
- 将 `TraceLogger`、`RoleplaySessionStore`、`WorksheetStore`、`ExposureStore` 改成 repository 接口；
- 保留 in-memory implementation 作为测试 fallback；
- 增加初始化脚本或启动时建表逻辑。

验证方式：

- `pytest` 全量通过；
- 重启后端后，已创建的 trace / worksheet / exposure plan 仍可查询；
- 确认 crisis 输入不会把高敏原文写入不必要的表。

### 阶段 2：User Memory

目标：保存用户近期练习状态和偏好，但避免存储过度敏感信息。

交付物：

- `GET /api/users/{user_id}/profile`；
- 用户近期场景、练习次数、最近 anxiety level、偏好难度；
- 隐私边界说明和可删除接口预留。

执行计划：

- 设计 `user_profiles`、`user_practice_summary` 表；
- 从 roleplay、worksheet、exposure attempts 中聚合轻量状态；
- 不保存诊断标签，不保存不必要的危机原文；
- 在 `/chat`、`/practice`、`/progress` 前端页面展示简短用户状态；
- 在 README 中补充 memory 数据边界。

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

### 阶段 4：Campus Resource RAG

目标：增加校内支持资源导航能力，但只使用 demo 文档，不编造真实学校电话或不存在资源。

交付物：

- `backend/data/knowledge_base/campus_resources/`；
- demo markdown 文档，例如 `counseling_center_demo.md`、`advisor_support_demo.md`、`peer_support_demo.md`；
- `POST /api/campus/query` 或扩展现有 `/api/knowledge/query`；
- 前端资源查询入口。

执行计划：

- 每篇文档继续使用 frontmatter，并标注 `Synthetic demo knowledge base`；
- RAG 响应必须带 citations；
- unknown 时明确说当前 demo 知识库不知道；
- 不生成真实电话、热线、地址或具体学校部门；
- crisis 查询仍优先走 Safety Policy / escalation。

验证方式：

- campus RAG 能检索 demo 资源；
- unknown query 返回 `unknown=true`；
- 测试确认不出现假联系方式；
- 前端展示 citations。

### 阶段 5：LLM 节点接入

目标：在保留 rule-based safety guardrails 的前提下，把部分 deterministic 节点升级为 LLM-backed agent。

交付物：

- LLM client 抽象；
- 可配置的 provider / model；
- LLM roleplay response；
- LLM worksheet extractor；
- 可选 LLM intent router fallback；
- prompt 和 safety policy 文档。

执行计划：

- 在 `BaseSafetyClassifier`、`BaseIntentRouter` 已预留接口基础上增加 LLM implementation；
- 默认仍使用 rule-based safety，LLM 只能作为辅助，不覆盖 crisis 拦截；
- 对 roleplay / worksheet 使用 RAG citations 作为 grounded context；
- 输出前做安全后处理，禁止诊断和效果承诺；
- 增加 `.env.example`，不提交真实 API key。

验证方式：

- 无 API key 时系统仍可用 rule-based MVP；
- LLM 输出不得绕过 crisis flow；
- eval cases 对比 rule-based 和 LLM-backed 节点；
- 手动测试 5 个典型场景。

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

### 阶段 7：Docker Compose 与部署

目标：让项目可以一键启动，方便演示、提交和部署。

交付物：

- `Dockerfile.backend`；
- `Dockerfile.frontend`；
- `docker-compose.yml`；
- SQLite volume 或 PostgreSQL service；
- README 一键启动说明。

执行计划：

- 后端容器运行 `uvicorn app.main:app --host 0.0.0.0`；
- 前端容器配置 `NEXT_PUBLIC_API_BASE_URL`；
- 可选加入 PostgreSQL；
- 增加健康检查；
- 保证 demo knowledge base 被打包进容器。

验证方式：

- `docker compose up --build` 可启动前后端；
- 浏览器访问前端；
- `/api/health` 和 `/docs` 可访问；
- pytest 可在容器或本地稳定运行。

### 阶段 8：展示材料和简历包装

目标：把工程能力和安全设计讲清楚，方便大创展示和简历呈现。

交付物：

- 项目架构图；
- agent workflow 图；
- demo script；
- screenshots；
- 简历 bullet points；
- 技术亮点说明。

执行计划：

- 画出 Safety → Router → Agent → RAG → Memory → Trace 的流程；
- 准备 3 条演示路径：普通社交压力、roleplay + feedback、crisis escalation；
- README 增加 demo walkthrough；
- 整理测试结果和 eval 指标；
- 总结安全边界和非医疗化设计。

验证方式：

- 按 demo script 可以 5 分钟内完整演示；
- README 能让新用户独立启动项目；
- 简历描述能体现 full-stack、agent workflow、RAG、安全和测试。
