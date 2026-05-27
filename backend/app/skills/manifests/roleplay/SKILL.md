# Roleplay Skill

## When to use

Use this skill when the user wants to simulate or rehearse a social scenario, such as classroom speech, group discussion, dorm conflict, club icebreaking, asking a teacher a question, refusing a request, or expressing disagreement.

## Inputs

- `user_id`
- `scenario`
- `difficulty`
- current user message
- session history
- retrieved social-skills guidance

## Output contract

- Next role-play turn.
- Session state persisted in SQLite.
- Structured feedback with clarity, naturalness, assertiveness, and empathy dimensions.
- Citations from social-skills or product-rubric knowledge.

## Safety boundaries

- Safety-check every user turn.
- Stop ordinary role-play on crisis input.
- Do not diagnose or promise therapeutic effects.
- Keep feedback behavioral and practice-oriented.

## Fallback behavior

Use deterministic scenario prompts and feedback if LLM generation fails or is disabled.
