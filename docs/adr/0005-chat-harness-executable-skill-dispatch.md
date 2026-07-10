# ADR 0005：Chat Harness 迁移到 Executable Skill Dispatch

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
- 前端 chat 需要处理 `roleplay_started`、`worksheet_created`、`consent_required` 等结构化 action；
- 高强度或写状态 action 必须先加入 permission 和 consent。

## 备选方案

- 让 `/api/chat` 只做导航：不采用，agent workflow 可信度不足。
- 把所有专业 API 行为直接塞进 `AgentHarness`：不采用，容易耦合和重复。
- 重建一个 runtime：不采用，已有 `AgentHarness` 是合适的扩展点。
