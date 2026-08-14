# ADR-001: PostgreSQL as source of truth

## Status
Accepted

## Context
U3 introduces Kafka as a distribution layer for `events` (ADR-002) and a transactional outbox
(ADR-003) to get rows onto it. Once a message queue is in the picture, something has to be
designated as authoritative when the two disagree — a message replayed twice, a message never
sent, a consumer that crashed mid-fold. UPGRADE_ARCHITECTURE.md assumes Postgres keeps this role
from v1; this ADR makes that assumption explicit and states what it forecloses now that a second
durable(-ish) system sits downstream of it.

## Options considered
1. **Postgres remains sole source of truth** (chosen). `events` (and `outbox_events` in the same
   transaction, ADR-003) are the only place a write is acknowledged as durable. Kafka carries a
   *copy* for fan-out; losing the topic entirely (wiped, misconfigured retention, a botched
   migration) loses no data, only live-update latency until consumers catch back up from Postgres.
2. **Kafka as source of truth**, Postgres as a downstream projection. Rejected: this is a much
   larger architectural change (exactly-once semantics, log compaction strategy, replaying the
   entire topic to rebuild `agents`/`policies`/auth state that has nothing to do with events) for
   no benefit BASTION actually needs — the `events` table already satisfies CLAUDE.md rule #1
   (event sourcing discipline) on its own.
3. **Dual-write, no designated authority.** Rejected outright: two systems that can each be
   "right" with no tiebreaker is exactly the split-brain problem ADR-002/003 exist to avoid.

## Decision
Postgres's `events` table is the only durable record of what happened. Every other representation
— `outbox_events`, the Kafka topic, `trace_summaries`, the aggregator's in-memory `active_traces`
— is a projection or a distribution copy, rebuildable from `events` by definition, never the
tiebreaker when something disagrees with it. `fold_events_to_graph` (graph.py) is intentionally
the single fold implementation used both for live tracking and for `GET /traces/{id}`'s on-demand
replay, so there is exactly one place "what actually happened" can be computed wrong.

## Consequences
- Kafka can be lost, replayed, or duplicated freely without data loss — only latency/staleness
  risk, bounded by how far behind consumers fall.
- Every consumer (`aggregator` group, `analytics`/`security` stubs) is free to re-derive full state
  from Postgres on any message rather than trust its own running fold, which is exactly what made
  U3's real bug (stale-broadcast from re-folding, see PROGRESS.md) fixable by going back to
  per-event derivation instead of inventing a second source of truth to reconcile against.
- This forecloses ever treating Kafka retention as a backup/archival strategy for `events` — it
  isn't one, and nothing in this design should start relying on it as one.

## Failure modes
Postgres down: no writes succeed anywhere in the system (`/intercept` fails closed, per
ARCHITECTURE.md's existing fail-closed policy stance) — this was already true pre-U3 and is
unchanged. Kafka down: writes still succeed and are durable in `outbox_events`; live WS updates and
analytics/security fan-out stall until Kafka recovers, at which point the outbox publisher and all
consumer groups resume exactly where their respective offsets/`published_at` state left off — no
special recovery procedure needed because nothing downstream of Postgres was ever authoritative.
