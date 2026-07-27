# ADR 0008: Unified conversation timeline and consent-gated module stack

- Status: Accepted
- Date: 2026-07-27

## Context

SocialEase currently exposes general support, role-play, worksheets, exposure
planning, and resource navigation through separate workflow or domain APIs. Users
need one continuous conversation in which those capabilities share context without
turning the application into an unconstrained mental-health chatbot.

Conversation history, bounded model context, and long-term agent memory have
different privacy and lifecycle requirements. Treating them as one store would
make deletion, consent, and model-context behavior difficult to explain.

## Decision

The application will use a user-owned `Conversation` as the top-level scope and an
ordered, append-only `ConversationEvent` timeline as its historical source of
truth. A bounded context assembler may select and compact timeline data, but that
projection is not the historical record. Long-term agent memory remains separately
consent-gated and records its source conversation when applicable.

The LLM may only suggest a strictly validated `ModuleProposal`. Starting a module
requires application policy checks and explicit user acceptance. The model cannot
create, accept, reject, complete, or terminate module runs.

Confirmed modules execute as a bounded stack. The top frame receives messages;
starting a permitted child suspends its parent; completing or explicitly
terminating the child resumes the parent. Users can terminate the top frame or all
frames. The maximum depth is three and parent-child combinations are allow-listed.

Crisis classification preempts general and module routing at every depth.
Conversation history is retained after a versioned persistence notice until the
user deletes it. Deleting a conversation must also remove its events, module
state, working context, content indexes, pending memory proposals, and derived
memory according to the documented deletion policy.

## Consequences

- Domain services remain independent and are connected through adapters.
- One conversation identifier is preserved while users enter and leave modules.
- Full history is available to users, while prompt context remains token-bounded.
- History persistence is not permission to create long-term memory.
- Every repository operation must bind both user and conversation ownership.
- Existing domain APIs remain during a migration window; new clients use the
  conversation gateway.
