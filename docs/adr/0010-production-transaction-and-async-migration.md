# ADR 0010: Production transaction model and asynchronous PostgreSQL migration

## Status

Accepted

## Context

The current product path combines synchronous database repositories, asynchronous
LLM/Redis/provider calls, and several independently committed state transitions.
Event-level idempotency does not make a complete user command idempotent, and a
partial failure can leave protocol, module, timeline, cache, and external-provider
state out of sync.

Conversation history, cross-conversation agent memory, operational traces, and
account-owned data also require different export, retention, and deletion policies.
They must not share one hand-maintained deletion list.

## Decision

The production architecture will use:

1. PostgreSQL as the only production source of truth.
2. SQLAlchemy `AsyncEngine` with psycopg 3 asynchronous connections throughout
   PostgreSQL repositories and application call chains.
3. One application command record per state-changing request, keyed by owner,
   aggregate, and idempotency key.
4. Atomic database state transitions inside explicit units of work.
5. An outbox and reconciliation flow for Redis and external-provider side effects.
6. Separate data inventories for conversation history, agent memory, operational
   traces, practice records, and complete account deletion.
7. Fail-closed production configuration. Demo adapters and SQLite are not valid
   production fallbacks.

No compatibility layer will preserve the deprecated stateless chat path after its
consumers are removed.

## Delivery stages

1. Correct deletion boundaries and runtime-cache erasure.
2. Atomically claim consent and rotate refresh tokens.
3. Add conversation command inbox and result replay.
4. Migrate PostgreSQL repositories and callers to `AsyncEngine`.
5. Consolidate module transitions and add outbox reconciliation.
6. Remove legacy chat, production demo fallbacks, and SQLite production support.
7. Harden identity, readiness, containers, dependencies, and failure-injection
   coverage.

Each stage must add regression tests before removing the superseded path.

## Consequences

- Repository protocols become asynchronous; call sites and tests must await them.
- SQLite may remain temporarily as a local/test adapter, but production capability
  checks will reject it before the adapter is removed.
- External side effects are eventually reconciled, but command acceptance and local
  state transitions remain deterministic and replayable.
- Data deletion endpoints become narrower and explicit, avoiding destructive
  compatibility behavior.
