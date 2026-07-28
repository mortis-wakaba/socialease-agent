# General Support Skill

## When to use

Use this skill for non-crisis unified conversation messages, especially general social stress, uncertainty about which practice mode to use, or lightweight emotional support.

## Inputs

- `user_id`
- `message`
- `intent`
- `safety_result`
- optional `request_context`

## Output contract

- A short, schema-validated, non-medical supportive response.
- Optional CBT-style fields that distinguish explicit thoughts from facts and predictions.
- One to three low-intensity steps with pause/exit support.
- Structured hints that may point to role-play, worksheet, exposure planning, support resource RAG, or progress review.

## Safety boundaries

- Do not diagnose mental disorders.
- Do not promise treatment effects.
- Do not discourage real-world support.
- Crisis messages must be routed to `crisis_escalation_skill` before this skill runs.

## Fallback behavior

The skill first retrieves reviewed `social_skills` guidance and uses bounded LLM generation.
Provider, JSON, or output-guardrail failures fall back to a deterministic support response.
