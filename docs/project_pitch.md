# SocialEase Agent Project Pitch

这份文档用于 README 摘要、答辩介绍、面试讲解和简历包装。

## 一句话介绍

SocialEase Agent 是一个面向大学生社交压力场景的安全可控全栈 Agent 系统，围绕 Agent Harness、Skill Registry 和 Eval Suite 组织 Safety Classifier、LLM-first Intent Router、RAG、Role-play、CBT 风格 worksheet、Exposure Planner、User Memory 和 Trace。

## 项目亮点

### 1. 高风险场景下的安全优先设计

- 明确非医疗化边界：不诊断、不承诺治疗、不替代咨询；
- crisis 输入绕过普通 agent，进入 escalation；
- hybrid safety：deterministic rules 提供不可降级底线，LLM 只能上调风险；
- 支持资源查询不编造学校电话、热线或不存在的服务。

### 2. 工程化 Agent Workflow

核心链路：

```text
User Input
  → Agent Harness
  → Safety Classifier
  → Intent Router
  → Skill Registry / Skill Dispatch
  → Knowledge RAG / Memory / Persistence
  → Trace Logger
  → Frontend UI
```

Registered skills / specialized agents：

- Chat / Support Agent；
- Role-play Agent；
- CBT Worksheet Agent；
- Exposure Planner；
- Support Resource RAG。

### 3. RAG 与知识库分层

知识库不是一锅端，而是按用途分层：

- `social_skills`：社交练习、CBT 风格反思、暴露练习；
- `support_resources`：真实公开支持资源；
- `safety_policy`：安全边界和 crisis policy；
- `product_rubrics`：roleplay feedback、worksheet extraction、eval 标准；
- `campus_resources_demo`：仅用于演示的数据形态，不冒充真实校园服务。

### 4. LLM 接入但不依赖 LLM 脆弱性

- Provider abstraction 支持 OpenAI-compatible API，例如 DeepSeek；
- `LLM_ENABLED=false` 时系统仍可完整运行 deterministic MVP；
- LLM 输出非法、调用失败或 key 缺失时自动 fallback；
- `llm_usage` 暴露每个节点是否使用 LLM、是否 fallback。

### 5. Agent Eval Suite 与可观察性

- Trace 页面展示 Safety → Router → Agent → Memory → Output；
- 后端保存 run trace，可通过 `GET /api/runs/{run_id}` 查询；
- Agent Eval Suite 覆盖 safety、safety red-team、intent、citation、roleplay feedback、worksheet extraction 和 E2E harness workflow；
- pytest 覆盖核心 classifier、router、RAG、API workflow 和 fallback。

### 6. 可运行的全栈交付

- FastAPI + Pydantic backend；
- Next.js + TypeScript + Tailwind frontend；
- SQLite repository persistence；
- Docker Compose 一键启动；
- README 提供本地运行、Docker 运行、API 示例和 demo walkthrough。

## 架构图

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

## 5 分钟讲解结构

1. **项目定位**：不是医疗产品，而是安全可控的社交压力练习 agent；
2. **安全边界**：crisis escalation、非医疗化、hybrid safety；
3. **核心 workflow**：Safety → Router → Agent → RAG → Memory → Trace；
4. **功能展示**：chat、role-play feedback、worksheet、support RAG、trace；
5. **工程质量**：tests、eval、fallback、Docker Compose；
6. **反思**：如果上线真实校园场景，需要审核后的资源导入和更严格隐私治理。

## 简历 bullet points

中文版本：

- 设计并实现面向大学生社交压力场景的安全可控全栈 Agent 系统，支持社交情境模拟、CBT 风格自助 worksheet、分级暴露练习、公开支持资源 RAG 和 crisis escalation。
- 构建 hybrid safety pipeline：deterministic rules 提供不可降级安全底线，LLM 仅允许上调隐晦风险；危机输入绕过普通 agent 并进入安全升级流程。
- 实现 OpenAI-compatible LLM provider abstraction，支持 DeepSeek 接入、LLM-first intent routing、role-play generation、worksheet extraction，并提供 deterministic fallback 和 `llm_usage` 可观察性。
- 设计分层知识库和 citation 机制，将 social skills、support resources、safety policy、product rubrics、demo campus resources 隔离，避免资源幻觉和虚构校园服务。
- 使用 FastAPI、Pydantic、SQLite repository、Next.js、TypeScript 和 Docker Compose 完成可一键运行的全栈交付，并通过 pytest/eval 覆盖 safety、routing、skills registry、RAG citation 和 agent workflow。

English version:

- Built a safety-controlled full-stack agent system for college social stress scenarios, supporting role-play practice, CBT-style worksheets, graded exposure planning, support-resource RAG, and crisis escalation.
- Designed a hybrid safety and permission pipeline where deterministic rules provide a non-degradable safety floor, LLM classification can only escalate subtle risks, and crisis inputs bypass normal routing and skills.
- Implemented an OpenAI-compatible LLM abstraction for DeepSeek-style providers, with LLM-first intent routing, role-play generation, worksheet extraction, deterministic fallback, harness hooks, skill manifests, and `llm_usage` observability.
- Designed a layered knowledge base with citations across social skills, public support resources, safety policy, product rubrics, and demo campus resources to reduce hallucinated resource claims.
- Delivered a runnable full-stack app with FastAPI, Pydantic, SQLite repositories, Next.js, TypeScript, Docker Compose, harness capability and metrics APIs, pytest coverage, a skill registry, production-readiness notes, ADRs, and a lightweight agent evaluation suite.

## 面试中可以主动讲的 tradeoffs

### 为什么没有把所有判断都交给 LLM？

因为心理健康相关场景需要不可降级的安全底线。LLM 语义能力强，适合识别隐晦风险；但 deterministic rules 更适合兜住明确危机表达。因此项目采用 hybrid design：最终风险等级取更保守结果。

### 为什么 demo 校园资源不直接当真实资源用？

因为虚构学校电话或服务会造成现实风险。当前项目只把校园资源作为 demo 数据形态，真实资源查询只使用公开可验证来源；未来接入具体学校时，需要审核后的导入流程。

### 为什么要做 eval？

Agent 项目不能只靠人工试用。eval 用来固定关键安全和质量要求，例如 crisis 必须被 blocked、citation 必须命中真实文档、unknown query 不能胡编。

### 如果继续迭代，会做什么？

- 引入真实学校审核资源导入后台；
- 更细的隐私与数据保留策略；
- 更完整的前端组件测试；
- 部署到云平台并增加 observability dashboard；
- 对 LLM prompt 和 eval dataset 做版本管理。
