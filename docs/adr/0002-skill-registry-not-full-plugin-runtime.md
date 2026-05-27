# ADR 0002: Skill Registry Instead of Full Plugin Runtime

## Context

The project needs to express role-play, worksheet, exposure planning, support RAG, and crisis escalation as agent capabilities. A full plugin runtime would add complexity before the MVP needs it.

## Decision

Use a lightweight `SkillRegistry` with:

- `SkillDescriptor` metadata;
- executable skills for chat harness dispatch;
- documented skill descriptors for existing feature APIs;
- on-demand `SKILL.md` manifests.

## Consequences

Benefits:

- makes capabilities discoverable;
- aligns the project with modern agent harness architecture;
- avoids overengineering a plugin system.

Tradeoffs:

- not all skills are executable through the same interface yet;
- some feature APIs still call their existing agents directly;
- future plugin loading would require a stronger contract.

## Alternatives Considered

- Keep APIs as isolated features: simpler, but weaker architecture story.
- Build dynamic plugin loading: too complex for current demo.
- Rewrite all feature APIs behind one skill interface: possible later, but risky as a large refactor now.
