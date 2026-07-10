# ADR 0006：先强化产品边界，再增加更多 Agent

## 背景

Harness 重构后，SocialEase 已经具备安全可控 agent runtime 的核心形态：`AgentHarness`、deterministic/optional LLM safety、intent routing、permission gate、consent protocol、executable skill dispatch、hooks、memory、trace、eval 和 Next.js action-aware frontend。

项目可以继续增加 specialist agents、MCP tools 或 plugin 机制。但 SocialEase 是心理健康相邻产品原型，当前主要风险不是 agent 数量不足，而是产品边界需要更强。

典型问题包括：

- routes 不能信任 client-supplied `user_id`；
- trace 和服务记录不能保留过多 raw user text；
- consent protocol 必须过期、绑定请求、绑定 session、一次性使用；
- 状态写入 continuation endpoint 需要更强 safety check；
- 面向产品的 trace 需要 redaction 和 owner-scoped access。

## 决策

在增加更多 agent 或外部工具前，优先强化产品边界：

1. 真实用户身份和 owner-scoped reads/writes；
2. 统一 privacy-aware persistence gate；
3. 所有状态写入入口补齐 safety checks；
4. consent protocol 升级为 scoped、expiring、one-time-use 状态机；
5. permission decision 可组合；
6. intervention-plan lifecycle 与 consent decision 对齐；
7. product-facing trace redaction 与受限审计；
8. 持久化和 metrics 支持多用户并发；
9. CI/eval gate 覆盖 privacy、ownership、consent 和 continuation-turn safety。

## 影响

优点：

- 符合安全敏感产品工程优先级；
- harness gate 从“提示性”变成“权威边界”；
- 避免在缺少业务需求时引入不必要的多 Agent 复杂度；
- 在加功能前先建立可测试边界。

权衡：

- 短期可见 AI 功能增加较少；
- 更多工作投入 policy、persistence、tests、authorization；
- 产品约束变强后，本地展示流程会更接近真实产品。

## 备选方案

- 现在增加更多 specialist agents：暂不采用，不能解决 owner scoping、privacy persistence、consent replay 或 trace leakage。
- 现在加入 MCP：暂不采用，当前本地 `KnowledgeService` 足够支撑 RAG；MCP 更适合真实外部工具接入后再做。
- 延后产品边界：不采用，SocialEase 的敏感场景要求 safety、privacy 和 consent 是运行时约束。

## 状态

已接受，并作为后续 product-hardening 的主线。
