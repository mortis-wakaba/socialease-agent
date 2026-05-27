# General Support Skill

## When to use

Use this skill for non-crisis `/api/chat` requests, especially general social stress, uncertainty about which practice mode to use, or lightweight emotional support.

## Inputs

- `user_id`
- `message`
- `intent`
- `safety_result`
- optional `request_context`

## Output contract

- A short, non-medical supportive response.
- Structured hints that may point to role-play, worksheet, exposure planning, support resource RAG, or progress review.

## Safety boundaries

- Do not diagnose mental disorders.
- Do not promise treatment effects.
- Do not discourage real-world support.
- Crisis messages must be routed to `crisis_escalation_skill` before this skill runs.

## Fallback behavior

This skill can run without LLM access through deterministic support responses.
