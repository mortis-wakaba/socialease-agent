# ADR 0002：使用 Skill Registry，而不是完整 Plugin Runtime

## 背景

项目需要把 role-play、worksheet、exposure planning、support RAG 和 crisis escalation 表达为 agent 能力。完整 plugin runtime 会在当前阶段引入过多复杂度。

## 决策

使用轻量 `SkillRegistry`：

- `SkillDescriptor` 描述能力 metadata；
- executable skills 供 chat harness dispatch；
- documented skill descriptors 连接已有 feature APIs；
- `SKILL.md` manifest 按需加载。

## 影响

优点：

- 能力可发现；
- 符合现代 agent harness 架构；
- 避免过早工程化 plugin system。

权衡：

- 不是所有 skill 都完全统一到同一个 interface；
- 部分 feature API 仍直接调用已有 agent/service；
- 未来若要动态加载 plugin，需要更强 contract。

## 备选方案

- 保持 API 完全分散：更简单，但架构表达弱。
- 动态 plugin loading：当前过重。
- 立刻把所有 feature API 重写到统一 skill interface：可行但风险大。
