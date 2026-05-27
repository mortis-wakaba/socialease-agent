# SocialEase Agent Harness Design

SocialEase follows a lightweight **Model + Harness** design inspired by agent-engineering patterns such as tools, skills, permission gates, hooks, memory, and evals.

The goal is not to copy a coding-agent architecture directly. SocialEase is a safety-sensitive social-stress support demo, so the harness is adapted around non-medical boundaries, crisis escalation, grounded resources, and auditable workflow traces.

## Core formula

```text
Agent = Model + Harness

Harness = Skills + Knowledge + Observation + Action Interfaces + Permissions
```

In SocialEase:

| Harness part | Project implementation |
|---|---|
| Skills | `backend/app/skills/`, `backend/app/agents/`, feature APIs |
| Knowledge | `backend/data/knowledge_base/`, `backend/app/knowledge/` |
| Observation | `TraceLogger`, `/api/runs/{run_id}`, `llm_usage`, eval metrics |
| Action Interfaces | FastAPI routes and Next.js pages |
| Permissions | `backend/app/safety/`, crisis escalation, non-medical output rules |

## Runtime loop

```text
User Input
  → AgentHarness
  → before_safety hooks
  → SafetyClassifier
  → SafetyPermissionGate
  → IntentRouter, unless permission requires escalation
  → SkillRegistry.resolve_for_chat(...)
  → Skill.run(...)
  → TraceLogger
  → after_trace hooks
  → API Response
```

The harness decides **what may happen**. The model, when enabled, helps with semantic classification, routing, generation, or extraction, but it does not own the safety boundary.

## Permission gate

`backend/app/safety/permissions.py` converts safety classification into a harness-level decision:

```text
low / medium / high → allow normal routing
crisis             → escalate, skip ordinary routing and skills
```

This makes crisis escalation a runtime permission decision rather than merely a response template.

Current actions:

- `ALLOW`
- `ESCALATE`

Future extensions could include:

- `BLOCK_MEDICAL_CLAIM`
- `REQUIRE_CITATION`
- `REQUIRE_HUMAN_REVIEW`

## Skill registry and manifests

`backend/app/skills/registry.py` registers executable and documented skills:

- `crisis_escalation_skill`
- `general_support_skill`
- `roleplay_skill`
- `worksheet_skill`
- `exposure_planning_skill`
- `support_resource_rag_skill`

Each skill can have an on-demand manifest:

```text
backend/app/skills/manifests/<skill>/SKILL.md
```

Manifests describe:

- when to use the skill;
- expected inputs;
- output contract;
- safety boundaries;
- fallback behavior.

The loader in `backend/app/skills/manifest_loader.py` keeps this knowledge lazy: callers can list skills by metadata first, then load detailed skill knowledge only when needed.

## Hooks

`backend/app/workflow/hooks.py` defines no-op-compatible lifecycle hooks around the harness loop:

- `before_safety`
- `after_safety`
- `after_routing`
- `after_skill`
- `after_trace`

Hooks are intentionally small. They provide future extension points for trace enrichment, audit logging, eval capture, or metrics without rewriting the main harness loop.

## Knowledge and grounding

SocialEase separates knowledge into layers:

- `social_skills`
- `support_resources`
- `safety_policy`
- `product_rubrics`
- `campus_resources_demo`

This separation is part of the harness boundary. Skills should retrieve from the correct layer and return citations when grounding matters. Demo campus resources must not be presented as real services.

## Observation and evals

Observation includes runtime trace and offline evals:

- `TraceLogger` stores each run;
- `/api/harness/capabilities` exposes runtime loop, permissions, skills, knowledge layers, and observation features;
- `/api/harness/metrics` aggregates recent runs, crisis count, fallback count, intent distribution, selected-agent distribution, and latency;
- `/trace` visualizes Safety → Router → Agent/Skill → Memory → Output;
- `llm_usage` shows whether LLM calls succeeded or fell back;
- `backend/app/evals/` checks safety, safety red-team cases, routing, citations, roleplay feedback, worksheet extraction, and E2E workflow behavior.

Eval requirements are part of the harness contract. For example, crisis cases should remain hard requirements, not subjective generation quality checks.

## What this design intentionally avoids

SocialEase does not currently implement:

- subagent teams;
- worktree isolation;
- shell/tool execution permissions;
- cron/background autonomous jobs;
- broad plugin installation.

Those patterns are useful for coding agents, but they would add complexity without serving the current safety-sensitive social-support demo.

## Current tradeoff

The project uses a lightweight registry rather than a full plugin runtime. This is intentional: it gives the project agent-harness structure without overengineering the MVP.
