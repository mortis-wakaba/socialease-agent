# SocialEase Production Readiness Gap Analysis

SocialEase is a **production-inspired prototype**, not a production mental-health service. This document explains what has already been designed with production concerns in mind, and what would be required before any real deployment.

## Current production-inspired controls

### Safety and escalation

Implemented:

- deterministic safety floor for explicit crisis expressions;
- optional LLM safety classifier that may only raise risk;
- `SafetyPermissionGate` that turns crisis into an `ESCALATE` harness decision;
- crisis inputs bypass ordinary routing and skills;
- non-medical crisis response that recommends real-world support.

Production gap:

- larger multilingual red-team dataset;
- human-reviewed crisis escalation protocol;
- institution-specific response workflow;
- clinical/legal review of wording.

### Grounding and resource integrity

Implemented:

- layered knowledge base;
- support-resource RAG restricted to public verified resources;
- citation metadata with source type and URL;
- `unknown=true` instead of hallucinating missing resources;
- demo campus resources separated from real public resources.

Production gap:

- audited campus-resource import workflow;
- resource freshness checks;
- owner/reviewer metadata;
- scheduled review and expiration policy.

### Reliability and fallback

Implemented:

- `BaseLLMClient` abstraction;
- OpenAI-compatible provider adapter;
- `LLM_ENABLED=false` default;
- deterministic fallback for routing, role-play, worksheet extraction, and safety;
- `llm_usage` metadata for visibility.

Production gap:

- retry policy and circuit breaker;
- rate-limit handling;
- provider-level monitoring;
- prompt/model versioning;
- rollout and rollback strategy.

### Observability

Implemented:

- `TraceLogger` for individual runs;
- `/api/runs/{run_id}` trace lookup;
- `/api/harness/capabilities` capability discovery;
- `llm_usage` on key LLM-backed nodes;
- deterministic eval suite.

Production gap:

- aggregated metrics dashboard;
- alerting for crisis/fallback spikes;
- structured logs with redaction;
- privacy-aware audit trail;
- SLO/SLA definitions.

### Data and privacy

Implemented:

- SQLite persistence for demo records;
- repository interfaces for future storage replacement;
- lightweight user profile summary;
- crisis text is not copied into ordinary memory summarization.

Production gap:

- consent flow;
- data retention policy;
- user deletion/export APIs;
- encryption at rest and in transit;
- access control and admin roles;
- privacy impact assessment.

### Evaluation and testing

Implemented:

- pytest for safety, routing, RAG, LLM fallback, skills, APIs, and harness behavior;
- eval suite for safety, routing, citation, unknown handling, roleplay feedback, worksheet extraction, and E2E workflow;
- bundled JSONL eval cases for deterministic regression.

Production gap:

- expanded red-team evals;
- adversarial prompt injection cases;
- longitudinal quality monitoring;
- human evaluation rubric;
- regression gates in CI.

## Deployment readiness

Current deployment is suitable for local/demo use:

```bash
docker compose up --build
```

Production deployment would require:

- managed database;
- secret management;
- HTTPS and CORS hardening;
- authentication/authorization;
- observability stack;
- backup and restore process;
- reviewed environment-specific configuration.

## Risk statement

SocialEase should not be presented as a medical product or crisis service. It is a demo of how to engineer a safety-aware LLM agent harness around a sensitive domain. A real deployment would require institutional, clinical, legal, privacy, and operational review.

## Suggested next production-hardening steps

1. Expand safety red-team evals and make crisis blocking a CI gate.
2. Add privacy-aware run metrics with redacted aggregation.
3. Add user data deletion/export endpoints.
4. Add reviewed campus-resource import and freshness workflow.
5. Add cloud deployment with secret management and monitoring.
