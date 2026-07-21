# ADR 0004：OpenAI-Compatible LLM Adapter 与 Deterministic Fallback

## 背景

项目需要支持 DeepSeek 或其他 OpenAI-compatible provider，同时不能把业务逻辑绑定到某个厂商。系统也必须在没有 API key 时可运行、可测试。

## 决策

采用：

- `BaseLLMClient` 作为 provider-agnostic interface；
- `OpenAICompatibleLLMClient` 适配 DeepSeek 风格 API；
- 默认 `LLM_ENABLED=false`；
- routing、role-play、worksheet extraction 和 safety 都有 deterministic fallback。

## 影响

优点：

- provider 可替换；
- 本地运行不依赖 secret；
- fallback 行为可测试；
- `llm_usage` 让 provider 使用情况可观察。

权衡：

- 不同 OpenAI-compatible API 仍可能有细节差异；
- deterministic fallback 不如 LLM 输出自然；
- 不同 Provider/模型升级仍需要独立灰度、质量监控与回滚策略。

## 当前实现检查点

- 生产 Prompt 已登记显式版本号与 AST 指纹 Manifest；
- CI 会拒绝 Prompt 内容变化但版本号未提升的提交；
- Trace 会记录应用版本、Prompt 版本、模型配置 Hash 与确定性 Eval 数据集版本；
- 这些能力解决可追溯性，但不替代真实 Provider 的灰度和回滚机制。

## 备选方案

- 在 agent 中硬编码 DeepSeek：不采用，厂商耦合过强。
- 所有功能都强依赖 LLM：不采用，不利于测试和展示。
- 直接接多个 provider SDK：接口面过大。
