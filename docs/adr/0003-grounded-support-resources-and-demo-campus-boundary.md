# ADR 0003: Grounded Support Resources and Demo Campus Boundary

## Context

A support-resource feature can easily hallucinate phone numbers, school offices, or services. That is unsafe in a mental-health-adjacent project.

## Decision

Separate knowledge layers:

- `support_resources` contains real, public, verifiable resources;
- `campus_resources_demo` contains sample campus data shape only;
- ordinary support-resource RAG does not present demo campus resources as real services;
- unknown queries return `unknown=true` instead of invented resources.

## Consequences

Benefits:

- reduces resource hallucination risk;
- makes demo limitations explicit;
- supports future audited campus-specific resource import.

Tradeoffs:

- demo may feel less locally personalized;
- requires careful citation display;
- real deployment would need institution-specific review.

## Alternatives Considered

- Invent a campus resource database: rejected as unsafe and misleading.
- Use only general support links: safe but less extensible.
- Let LLM answer resource questions freely: rejected due to hallucination risk.
