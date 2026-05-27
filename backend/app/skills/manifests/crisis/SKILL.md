# Crisis Escalation Skill

## When to use

Use this skill whenever the safety classifier or permission gate marks a message as `crisis`, including self-harm, suicide, harm-to-others, or severe immediate-risk expressions.

## Inputs

- `user_id`
- `message`
- `safety_result`

## Output contract

- A non-medical crisis escalation response.
- `structured_data.escalation = true`.
- Recommended real-world support actions.

## Safety boundaries

- Do not diagnose.
- Do not provide treatment instructions.
- Do not continue ordinary role-play, worksheet, exposure, or resource search flows.
- Encourage contacting trusted people, school counseling/support staff, or local emergency services.

## Fallback behavior

This skill is deterministic and must not require an LLM call.
