# ADR 0005：Chat Harness 迁移到 Executable Skill Dispatch

- 状态：产品入口部分已由 [ADR 0008](0008-unified-conversation-timeline-and-module-stack.md)
  取代；Executable Skill Dispatch 作为内部 Harness 能力继续保留。

## 背景

早期 SocialEase 有两条路径：

- `/api/chat` 执行主 harness：safety、permission、routing、support/crisis 和 trace；
- `/api/roleplay`、`/api/worksheet`、`/api/exposure`、`/api/support/query` 直接从 FastAPI route 调用专业能力。

这会导致 `/api/chat` 能识别专业 intent，但不能真正执行对应 skill。为了让主入口更像 agent runtime，需要把专业能力接入 executable skill dispatch。

## 决策

分阶段迁移：

1. 抽取 role-play、worksheet、exposure、support resources 的 service class，让 API routes 和 harness skills 共享业务逻辑。
2. 增加 role-play、worksheet、exposure planning、support RAG 的 executable skills。
3. 更新 `SkillRegistry.resolve_for_chat()`，让 intent routing 分发到专业 skill。
4. 更新 E2E eval，从只检查 `support_agent` 改为检查实际 selected specialized agent。
5. Trace 增加 selected skill/action metadata。

## 安全约束

- Crisis 输入必须绕过普通 skill 并进入 escalation；
- 专业 skill 不能削弱 deterministic safety floor；
- exposure 和 role-play 默认保持低强度；
- memory write 必须 privacy-minimized，不保存不必要 crisis text。

## 影响

优点：

- `/api/chat` 成为真正 lead harness entrypoint；
- 专业页面继续可用，同时复用 service logic；
- E2E eval 能评测实际 skill dispatch；
- Trace 更适合展示和调试。

权衡：

- dispatch 行为改变时，测试和 eval fixture 需要同步；
- 旧前端 chat 曾需要处理专业模块 action；统一会话迁移后只处理
  `use_unified_conversation` 导航结果；
- 高强度或写状态 action 必须先加入 permission 和 consent。

## ADR 0008 后的现状

上面的“专业页面和领域写 API 继续可用”仅记录当时的迁移决策，现已结束。当前产品消息
只通过 Conversation Gateway 写入；公开领域写 API 已移除，旧专业页面重定向到
`/chat`。deprecated `/api/chat` 中的 roleplay、worksheet、exposure 和 resource skills
均只返回 `use_unified_conversation`，不创建 Domain Session 或执行脱离时间线的模块。

## 备选方案

- 让 `/api/chat` 只做导航：不采用，agent workflow 可信度不足。
- 把所有专业 API 行为直接塞进 `AgentHarness`：不采用，容易耦合和重复。
- 重建一个 runtime：不采用，已有 `AgentHarness` 是合适的扩展点。
