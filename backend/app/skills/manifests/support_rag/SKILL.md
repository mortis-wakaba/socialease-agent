# Support Resource RAG Skill

## When to use

Use this skill when the user asks for public support resources, non-medical social-practice guidance, or where to seek help in a non-crisis situation.

## Inputs

- `query`
- original user query
- safety result

## Output contract

- Grounded answer.
- Citations with source name, type, and URL.
- `unknown = true` when the knowledge base does not contain enough information.
- `blocked = true` for crisis input.
- Trace-safe summaries of model decisions, tool observations, and stop reason.

## Bounded loop

- The model may choose `search_support_resources`, `search_practice_guidance`, or `finish`.
- Retrieval tools are read-only and allow-listed by application code.
- The loop runs for at most three model decisions.
- Final text is composed from selected grounded observations rather than free model generation.

## Safety boundaries

- Do not invent phone numbers, offices, or school services.
- Do not present demo campus resources as real services.
- Crisis input must go to escalation rather than ordinary resource retrieval.
- The loop cannot write memory, start practice, or change user state.

## Fallback behavior

When LLM support is disabled, model output is invalid, a provider/tool fails, or the step budget is exhausted, return the existing deterministic support-resource query. Return unknown instead of hallucinating unsupported resources.
