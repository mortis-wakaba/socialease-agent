# ADR 0001: Hybrid Safety and Permission Gate

## Context

SocialEase 处理的是心理健康相关但非医疗化的社交压力场景。系统必须避免诊断、治疗承诺和普通 agent 对 crisis 输入的继续处理。

## Decision

Use a hybrid safety design:

- deterministic rules provide a non-degradable safety floor;
- optional LLM safety classification may raise risk but cannot lower deterministic risk;
- `SafetyPermissionGate` converts `crisis` risk into an `ESCALATE` harness decision;
- crisis escalation bypasses ordinary routing and skill execution.

## Consequences

Benefits:

- explicit crisis hard requirement;
- more reliable than pure LLM classification;
- easy to test and explain in interviews.

Tradeoffs:

- rules may be conservative;
- subtle non-crisis nuance still depends on LLM quality when enabled;
- permission actions are currently simple and may need expansion for production.

## Alternatives Considered

- Pure LLM safety classification: rejected because it can be unstable and hard to guarantee.
- Pure keyword rules: rejected because subtle semantic risk may be missed.
- Human approval for every risky message: too heavy for MVP, but possible for production review workflows.
