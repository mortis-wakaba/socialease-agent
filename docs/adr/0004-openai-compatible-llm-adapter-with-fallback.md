# ADR 0004: OpenAI-Compatible LLM Adapter with Deterministic Fallback

## Context

The project should support DeepSeek or other OpenAI-compatible providers without coupling business logic to a vendor. It must also run without an API key for demos and tests.

## Decision

Use:

- `BaseLLMClient` as the provider-agnostic interface;
- `OpenAICompatibleLLMClient` for DeepSeek-style APIs;
- `LLM_ENABLED=false` by default;
- deterministic fallback for routing, role-play, worksheet extraction, and safety.

## Consequences

Benefits:

- provider can be changed without rewriting business agents;
- local demo works without secrets;
- fallback behavior is testable;
- `llm_usage` makes provider usage observable.

Tradeoffs:

- OpenAI-compatible APIs may differ slightly between providers;
- deterministic fallback is less fluent than LLM output;
- prompts and evals need versioning if the project grows.

## Alternatives Considered

- Hardcode DeepSeek into agents: rejected due to vendor coupling.
- Require LLM for all features: rejected because demos/tests should work without API keys.
- Use multiple provider SDKs directly: too much surface area for MVP.
