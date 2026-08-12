# Exposure Planning Skill

## When to use

This manifest describes the legacy `SkillRegistry` boundary for exposure intent. It redirects the user to the unified Conversation workflow; it does not create or update an exposure plan. After the user accepts a typed Module Proposal, the Conversation Exposure Adapter owns execution.

## Inputs

- `user_id`
- current user message

## Output contract

- `use_unified_conversation` navigation result.
- No plan, attempt, protocol, or memory mutation.

The Conversation Exposure Adapter, not this skill entrypoint, consumes a confirmed proposal and invokes the domain service that creates the bounded, stoppable practice ladder.

## Safety boundaries

- Do not frame tasks as treatment.
- Do not force exposure or encourage unsafe escalation.
- Always include stoppable fallback tasks.
- Block crisis input before creating or updating a normal plan.

## Fallback behavior

Return the deterministic unified-conversation navigation result; this boundary does not invoke an LLM.
