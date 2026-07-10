# ADR 0001：混合 Safety 与 Permission Gate

## 背景

SocialEase 面向心理健康相关但非医疗化的社交压力场景。系统必须避免诊断、治疗承诺，并确保 crisis 输入不会进入普通 agent 流程。

## 决策

采用混合 safety 设计：

- 规则分类器提供不可降级的 safety floor；
- 可选 LLM safety classification 只能提高风险等级，不能降低规则风险；
- `SafetyPermissionGate` 将 `crisis` 转成 `ESCALATE` harness decision；
- crisis escalation 绕过普通 routing 和 skill execution。

## 影响

优点：

- crisis 是硬约束；
- 比纯 LLM 分类更可控；
- 易于测试、审计和维护。

权衡：

- 规则可能偏保守；
- 细微语义仍依赖启用时的 LLM 质量；
- permission action 还需要随着产品策略继续扩展。

## 备选方案

- 纯 LLM safety classification：不采用，因为稳定性和可证明性不足。
- 纯关键词规则：不采用，因为可能漏掉隐式风险。
- 所有风险消息都人工审批：当前过重，但未来可用于生产审查流程。
