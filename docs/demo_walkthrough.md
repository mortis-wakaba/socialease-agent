# SocialEase Agent Demo Walkthrough

这个 walkthrough 用于 5 分钟内展示 SocialEase Agent 的核心能力：安全边界、Agent workflow、RAG citation、记忆/进度和可观察性。

## 演示前准备

启动项目：

```bash
docker compose up --build
```

访问：

- 前端：<http://127.0.0.1:3000>
- 后端文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

如果不配置 LLM API key，项目仍会使用 deterministic fallback 跑通完整 demo。启用 DeepSeek / OpenAI-compatible LLM：

```bash
LLM_ENABLED=true LLM_API_KEY=你的_api_key docker compose up --build
```

## 1. 开场说明：项目定位

推荐话术：

> SocialEase Agent 是一个面向大学生社交压力场景的安全可控 Agent 系统。它不是医疗产品，不做诊断，不承诺治疗效果。项目重点不是“心理聊天机器人”，而是工程化 agent workflow：Safety、Intent Router、RAG、Role-play、CBT 风格 worksheet、Exposure Planner、Memory、Trace 和 Evaluation。

可以强调三点：

- 心理健康相关功能必须非医疗化；
- 危机表达必须进入 crisis escalation；
- 所有资源导航都需要 citation，不编造学校电话或服务。

## 2. 演示路径 A：普通社交压力输入

页面：`/chat`

示例输入：

```text
我明天要在课堂上发言，怕自己说不清楚，也怕同学觉得我很尴尬。
```

展示点：

- Safety 判断为普通/低风险，不进入 crisis；
- Intent Router 将请求路由到合适的支持/练习方向；
- 回复保持非医疗化：做情境拆解、下一步建议，不给诊断；
- 页面显示 `risk_level`、`intent`、`run_id` 和 LLM usage。

讲解重点：

> 这一步展示的是普通 agent workflow。系统先做 safety，再做 intent routing，而不是直接把用户输入丢给模型生成。这样可以保证安全边界和可解释性。

## 3. 演示路径 B：Role-play + Feedback

页面：`/practice`

操作：

1. 选择 `classroom_speech` 或 `group_discussion`；
2. 选择难度 2 或 3；
3. 开始 session；
4. 输入一轮对话。

示例用户输入：

```text
我想先说我的核心观点：这个方案的优点是可以让小组分工更清楚。
```

展示点：

- 创建 session 时会检索 `social_skills` 知识库；
- 对话轮次会保存到 SQLite；
- feedback 包含 clarity、naturalness、assertiveness、empathy 等维度；
- feedback 带 citations，说明依据来自项目自写 rubric / social skills 文档；
- 页面显示 turn-level LLM usage 和 citations。

讲解重点：

> 这里不是简单聊天，而是面向社交场景的 structured practice。反馈维度固定，来源可追踪，输出不会变成诊断或治疗建议。

## 4. 演示路径 C：CBT 风格 Worksheet

页面：`/worksheet`

示例输入：

```text
情境：明天课堂发言。
自动想法：我肯定会说错被大家笑。
情绪：焦虑。
强度：7/10。
支持证据：之前发言卡过壳。
反对证据：上次小组讨论同学认真听我说完。
替代想法：我可能会紧张，但可以先说核心观点。
下一步：今晚练习开场两遍。
```

展示点：

- Worksheet 将自然语言整理为结构化字段；
- 缺失字段时会温和追问，而不是强行补全；
- 输出包含 disclaimer；
- crisis 输入不会创建普通 worksheet。

讲解重点：

> 这个模块展示 validated extraction：LLM 可以参与抽取，但必须返回严格字段；输出不合法或调用失败时回退到 rule-based extractor。

## 5. 演示路径 D：Support Resource RAG

页面：`/support`

示例输入：

```text
social anxiety CBT self-help public resource
```

展示点：

- 只查询真实公开资源层 `support_resources`；
- citations 显示来源类型，例如 external public；
- 查不到时返回 unknown；
- 不把 demo 校园资源冒充成真实学校服务。

讲解重点：

> 资源导航比普通 RAG 更需要边界。项目把真实公开资源、项目自写规则、demo 校园资源分层，避免幻觉式补全。

## 6. 演示路径 E：Crisis Escalation

页面：`/chat` 或 `/practice`

示例输入应谨慎使用，可以用非具体方法的表达：

```text
我现在觉得撑不下去了，可能会伤害自己。
```

展示点：

- Safety 识别高风险；
- 系统绕过普通 agent；
- 返回 crisis escalation；
- 建议联系可信任的人、学校心理中心或当地紧急服务；
- 不继续 role-play、worksheet 或普通资源检索。

讲解重点：

> 这是项目最重要的安全底线。deterministic rules 不可被 LLM 降级；LLM 只能上调风险，不能降低危机等级。

## 7. Trace 与 Evaluation

页面：`/trace`

操作：复制前面 `/chat` 返回的 `run_id`，到 `/trace` 查询。

展示点：

- Safety → Router → Agent → Memory → Output 流程；
- 每个节点有可观察 metadata；
- 显示 safety/router 的 LLM usage；
- 可以解释一次 agent run 是如何被处理的。

本地评测：

```bash
cd backend
python -m app.evals.run
pytest
```

展示指标：

- safety accuracy；
- blocked crisis rate；
- intent accuracy；
- citation hit rate；
- unknown precision；
- roleplay feedback pass rate；
- worksheet extraction pass rate。

## 8. 结束总结

推荐话术：

> 这个项目的重点是把 LLM 应用做成一个可控系统：有安全边界、有 fallback、有引用、有 trace、有 eval，也有完整前后端和 Docker Compose。它不试图替代专业心理服务，而是演示如何在高风险领域里做谨慎的 agent workflow。
