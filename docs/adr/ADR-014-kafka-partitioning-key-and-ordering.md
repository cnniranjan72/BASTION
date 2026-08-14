# ADR-014: Kafka partitioning key (trace_id vs agent_id) and ordering guarantees

## Status
Accepted

## Context
Kafka only guarantees message ordering within a single partition, not across an entire topic. The
outbox publisher (ADR-003) has to pick a partition key for every message it sends on `tool-events`
(ADR-002). Whatever's chosen determines what ordering guarantee downstream consumers — chiefly the
aggregator's `fold_events_to_graph`, which processes a trace's events in sequence — can actually
rely on, and what they explicitly cannot.

## Options considered
1. **Partition by `trace_id`** (chosen). Every event belonging to the same trace — root span and
   every nested call within it — lands in the same partition, so they're delivered to any given
   consumer in the same relative order Postgres assigned them (`sequence_number`). No ordering
   guarantee across different traces, even from the same agent.
2. **Partition by `agent_id`.** Would group all of one agent's traces together, which sounds
   useful for per-agent ordering but isn't what any consumer actually needs — `fold_events_to_graph`
   operates one `trace_id` at a time regardless. It would also concentrate all of a single busy
   agent's throughput onto one partition, a worse load-distribution property than trace_id for
   agents that run many concurrent traces.
3. **No explicit key (round-robin/random partitioning).** Rejected outright: this gives no ordering
   guarantee even within a single trace, which `fold_events_to_graph`'s sequential processing
   assumption depends on (a `CallCompleted` arriving before its own `CallAttempted` would break the
   fold's node-lookup logic in graph.py, which expects `CallAttempted` to have created the node
   first).
4. **Single partition, whole topic.** Trivially gives total ordering but eliminates the entire
   point of partitioning — every consumer group is bottlenecked on one partition's throughput, and
   Kafka's parallelism (multiple consumers in a group, each owning a subset of partitions) becomes
   unusable. Not adopted; `tool-events` is provisioned with partitioning enabled from the start.

## Decision
`OutboxPublisher.publish_batch` sends every message with `key=str(row["trace_id"])`
(`outbox_publisher.py`). Kafka's default partitioner hashes the key to select a partition
deterministically, so every event for a given trace always lands in the same partition regardless
of which outbox batch or publisher instance sent it. Ordering is guaranteed within a trace, and
explicitly not guaranteed across different traces — consumers must not assume a global event
order, only a per-trace one.

## Consequences
- `fold_events_to_graph`'s sequential-fold assumption is sound for any single trace under this
  scheme, even though the aggregator's actual per-message handling
  (`_handle_notification`) re-fetches and re-folds from Postgres rather than relying on Kafka's
  delivery order alone (ADR-001's re-derive discipline) — partition ordering is a performance/
  correctness-of-broadcast property, not the sole thing standing between the system and a wrong
  fold.
- Two traces from the same agent, or even two concurrently running root-level operations, can be
  processed by a consumer in either relative order, or interleaved — never a bug, since nothing in
  the data model treats cross-trace ordering as meaningful.
- Local dev's single-node, single-partition-by-default Kafka setup doesn't currently exercise the
  multi-partition case at all (everything trivially lands on the one partition that exists) — the
  partitioning key choice is validated by design/code review here, not yet by a test that actually
  spans multiple partitions. Flagged as a known gap rather than silently assumed covered.

## Failure modes
A trace whose events span an unusually long wall-clock time (a long-running nested call) still
partitions consistently — the key is the trace_id itself, not a timestamp, so there's no windowing
effect that could split one trace's events across partitions over time. If the topic is ever
reconfigured with a different partition count, the hash-to-partition mapping for existing keys can
change, which would only affect newly-produced messages' placement relative to old ones already on
disk — already-published messages don't move, and per-trace ordering within whichever partition a
given message lands on is unaffected, since a trace either entirely predates or entirely postdates
a repartition in practice (traces don't span outages of that kind).
