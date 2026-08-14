# ADR-003: Transactional outbox pattern

## Status
Accepted

## Context
Given ADR-002's decision to publish every event to Kafka, something has to bridge "a row was
written to Postgres" to "a message was sent to Kafka" without creating a window where one can
happen without the other — the classic dual-write problem: a service that writes to its database
and then separately calls out to a message broker can crash between the two, silently losing the
message (DB committed, broker call never happened) or, if ordered the other way, publish a message
for a write that then fails to commit.

## Options considered
1. **Transactional outbox** (chosen): write the event row and an `outbox_events` row in one
   Postgres transaction, then a separate poller publishes unpublished rows to Kafka and marks them
   published only after a confirmed send. The two writes either both commit or neither does —
   there is no window where an event exists without a corresponding outbox intent, or vice versa.
2. **Two-phase commit across Postgres and Kafka.** Rejected: Kafka doesn't participate in XA-style
   distributed transactions in any practically usable way here, and 2PC's own failure modes (a
   coordinator crash mid-commit) are worse than the problem being solved.
3. **Change Data Capture (Debezium reading the Postgres WAL).** A well-known way to implement the
   outbox pattern without a bespoke poller, but it's substantial new infrastructure (a Kafka
   Connect deployment) for a system already standing up its first Kafka broker in this same phase —
   deferred as a possible future optimization if `outbox_events` polling latency ever becomes a
   real bottleneck, not adopted speculatively now.
4. **Publish-then-write** (publish to Kafka first, write to Postgres second). Rejected: this
   inverts ADR-001 — a message could reach consumers for an event that then fails to persist,
   making Kafka briefly more authoritative than Postgres, which is the one thing ADR-001 rules out.

## Decision
`interceptor/db.py`'s `insert_event` writes both the `events` row and an `outbox_events` row
(`event_id`, `trace_id`, `span_id`, `parent_span_id`, `agent_id`, `event_type`, `payload`,
`published_at NULL`) inside a single `asyncpg` transaction. A separate `OutboxPublisher` process
polls `WHERE published_at IS NULL ORDER BY id LIMIT $1`, sends each row to Kafka via
`send_and_wait` (backpressure — confirms the broker accepted it before moving on), and marks the
whole batch `published_at = now()` only after every message in it sent successfully. Resumability
is entirely a property of this Postgres state, not anything held in the publisher process's
memory — a crash mid-batch just leaves some rows unpublished, and the next run (this process
restarted, or a fresh instance) picks up exactly where the last one left off via the same query.

## Consequences
- At-least-once, not exactly-once, by construction: a crash between `send_and_wait` succeeding for
  some messages in a batch and the batch's `mark_outbox_events_published` call resends those
  already-sent messages on restart. The other direction never happens — a row is never marked
  published without a confirmed send, so no event is ever silently lost. Proven directly by
  `interceptor/tests/test_outbox_resumability.py`.
- The publisher is a separate deployable/process from the interceptor, matching CLAUDE.md rule #4
  (`/intercept` never blocks on non-essential work) — `insert_event`'s outbox write is a fast local
  transaction, no network call to Kafka on the request-handling path at all.
- `outbox_events` grows unboundedly unless pruned; no pruning/archival policy exists yet as of U3 —
  acceptable for the current phase, flagged here rather than silently deferred.

## Failure modes
Interceptor crashes after committing the transaction but before the publisher notices: no data
lost, the row is simply unpublished until the next poll — normal operation, not a failure case.
Publisher crashes mid-batch: exactly the scenario the milestone test simulates directly (a fresh
`OutboxPublisher` instance is constructed after only some rows in a batch are published) — some
rows already-published stay published, the rest remain `published_at IS NULL` and get picked up by
the next poll, whether that's this same process resuming or a full restart. Postgres itself down:
`insert_event`'s transaction fails outright, same fail-closed behavior as any other write on the
`/intercept` path — no partial state (an `events` row without its `outbox_events` row) is possible
because both writes are one transaction.
