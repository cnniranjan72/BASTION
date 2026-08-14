# ADR Template

Copy this format for every ADR under `docs/adr/`. Filename: `ADR-XXX-short-title.md`.

```md
# ADR-XXX: <Title>

## Status
Proposed | Accepted | Superseded by ADR-YYY

## Context
What problem or question forced this decision? What constraints applied?

## Options considered
1. Option A — pros/cons
2. Option B — pros/cons
3. Option C — pros/cons

## Decision
Which option was chosen, stated plainly in one or two sentences.

## Consequences
What this decision makes easier, what it makes harder, what it forecloses.

## Failure modes
What happens when a component this decision depends on fails? What's the degraded behavior?
```

Keep each ADR under one page. If you need more than a page, the decision is probably two decisions — split it.
