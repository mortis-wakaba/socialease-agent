# Worksheet Skill

## When to use

This manifest describes the legacy `SkillRegistry` boundary for worksheet intent. It redirects the user to the unified Conversation workflow; it does not extract or persist worksheet fields. After the user accepts a typed Module Proposal, the Conversation Worksheet Adapter owns execution.

## Inputs

- `user_id`
- current user message

## Output contract

- `use_unified_conversation` navigation result.
- No worksheet record, protocol, or memory mutation.

The Conversation Worksheet Adapter, not this skill entrypoint, invokes the domain service for structured fields, missing-field prompts, persistence, and grounding after module confirmation.

## Safety boundaries

- Do not infer diagnosis.
- Do not invent missing facts.
- Do not create ordinary worksheet records for crisis input.
- Keep language non-medical and user-controlled.

## Fallback behavior

Return the deterministic unified-conversation navigation result; this boundary does not invoke an LLM.
