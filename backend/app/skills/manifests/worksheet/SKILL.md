# Worksheet Skill

## When to use

Use this skill when the user wants to organize a stressful social situation into a CBT-style self-reflection worksheet or thought record.

## Inputs

- `user_id`
- natural-language worksheet message
- optional LLM extractor
- CBT reflection guidance retrieved from knowledge base

## Output contract

- Structured worksheet fields.
- Missing-field list when the user did not provide enough information.
- Gentle follow-up questions.
- Disclaimer that this is self-help reflection, not medical diagnosis or treatment.
- Citations where grounding is used.

## Safety boundaries

- Do not infer diagnosis.
- Do not invent missing facts.
- Do not create ordinary worksheet records for crisis input.
- Keep language non-medical and user-controlled.

## Fallback behavior

Use rule-based extraction when LLM output is invalid, unavailable, or disabled.
