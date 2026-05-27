# Support Resource RAG Skill

## When to use

Use this skill when the user asks for public support resources, self-help resources, or where to seek help in a non-crisis situation.

## Inputs

- `query`
- `kb_type = support_resources`
- safety result

## Output contract

- Grounded answer.
- Citations with source name, type, and URL.
- `unknown = true` when the knowledge base does not contain enough information.
- `blocked = true` for crisis input.

## Safety boundaries

- Do not invent phone numbers, offices, or school services.
- Do not present demo campus resources as real services.
- Crisis input must go to escalation rather than ordinary resource retrieval.

## Fallback behavior

Return unknown instead of hallucinating unsupported resources.
