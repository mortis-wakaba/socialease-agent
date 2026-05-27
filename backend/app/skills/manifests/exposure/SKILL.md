# Exposure Planning Skill

## When to use

Use this skill when the user wants a gradual, safe, stoppable social-practice ladder for a specific social goal.

## Inputs

- `user_id`
- target scenario
- current anxiety level
- previous attempts
- task feedback such as completed, skipped, or too hard

## Output contract

- A 5-7 step practice ladder from easier to harder.
- Each task includes difficulty, estimated time, success criteria, fallback task, and citations.
- Feedback updates the suggested next task.

## Safety boundaries

- Do not frame tasks as treatment.
- Do not force exposure or encourage unsafe escalation.
- Always include stoppable fallback tasks.
- Block crisis input before creating or updating a normal plan.

## Fallback behavior

Use deterministic planning rules when LLM is unavailable; preserve citations from social-skills guidance.
