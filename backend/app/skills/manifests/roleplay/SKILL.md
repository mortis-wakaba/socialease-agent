# Roleplay Skill

## When to use

This manifest describes the legacy `SkillRegistry` boundary for role-play intent. It redirects the user to the unified Conversation workflow; it does not execute a role-play turn or persist a session. After the user accepts a typed Module Proposal, the Conversation Role-play Adapter owns execution.

## Inputs

- `user_id`
- current user message

## Output contract

- `use_unified_conversation` navigation result.
- No Domain Session, transcript, feedback, or memory mutation.

The Conversation Role-play Adapter, not this skill entrypoint, consumes confirmed scenario and difficulty, writes lifecycle events to the unified timeline, and invokes the domain service for turns and feedback.

## Safety boundaries

- Safety-check every user turn.
- Stop ordinary role-play on crisis input.
- Do not diagnose or promise therapeutic effects.
- Keep feedback behavioral and practice-oriented.

## Fallback behavior

Return the deterministic unified-conversation navigation result; this boundary does not invoke an LLM.
