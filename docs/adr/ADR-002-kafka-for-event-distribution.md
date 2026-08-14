# ADR-002: Kafka for event distribution (not source of truth)

## Status
Accepted

## Context
v1's aggregator subscribed directly to Postgres `LISTEN`/`NOTIFY` (listener.py) to trigger
live-tracking folds and WS fan-out. That design has two limits UPGRADE_ARCHITECTURE.md §4 flags as
in scope for v2: it's a single-consumer mechanism (no independent "analytics" or "security" group
can replay the same stream on its own schedule without becoming a second listener competing for
the same notifications), and it has no replay/history — a consumer that wasn't listening at the
moment of a `NOTIFY` has simply missed it, with no `--from-beginning` equivalent.

## Options considered
1. **Kafka topic, at-least-once, consumer groups** (chosen). Independent consumer groups each get
   the full stream and their own offset; a fresh group can replay full history from the beginning
   (proven directly by `aggregator/tests/test_kafka_resumability.py`'s
   `test_fresh_analytics_consumer_replays_full_history_from_beginning`). Ordering guaranteed within
   a partition (ADR-014), not across partitions/traces — an accepted, explicit trade a single
   Postgres `NOTIFY` stream never had to make.
2. **Keep LISTEN/NOTIFY, add a second listener per consumer.** Rejected: `NOTIFY` has no delivery
   guarantee (a connection gap silently drops notifications) and no replay — every new consumer
   type would need its own bespoke "catch up from Postgres directly, then switch to live" bootstrap
   logic that Kafka's consumer-group model already provides for free.
3. **A different broker (Redis Streams, NATS, RabbitMQ).** Redis is already in the architecture as
   explicitly ephemeral acceleration (§4.2) — reusing it here would blur that boundary the same way
   ADR-005 rejected it for idempotency. Kafka's partition/consumer-group model maps directly onto
   "independent groups replay the same ordered stream," which is the actual requirement; a queue
   broker (RabbitMQ) does not offer that replay property without extra plugins bolted on.

## Decision
`tool-events`, a single Kafka topic (KRaft-mode, single-node in dev — `infra/docker/docker-compose.yml`),
carries every event written to Postgres via the transactional outbox (ADR-003). LISTEN/NOTIFY
(listener.py) is retired from the live path — kept in the tree, unwired from `main.py`'s
lifespan — rather than kept as a parallel fallback: a fallback path that isn't exercised in
production is exactly the kind of code CLAUDE.md's "no mocked/faked integration past early phases"
spirit warns against, and having two live-notification mechanisms active at once would reintroduce
the ordering-ambiguity problem ADR-001 exists to avoid, not reduce risk.

## Consequences
- New consumer types (`analytics`, `security` — real stub groups as of U3, business logic
  deliberately out of scope, see `stub_consumers.py`) are additive: a new `group_id` is the entire
  integration cost, no changes to the write path.
- At-least-once delivery becomes a first-class fact every consumer must handle, not an edge case —
  addressed by ADR-001's re-derive-from-Postgres discipline rather than by trying to make Kafka
  delivery exactly-once.
- Losing LISTEN/NOTIFY as the fallback means Kafka availability now gates live WS updates
  end-to-end; accepted because Postgres (the actual source of truth) is unaffected by a Kafka
  outage (ADR-001's failure-mode analysis).

## Failure modes
Kafka broker unreachable: the outbox publisher's `run_forever` loop keeps polling and simply finds
nothing it can send (rows accumulate as unpublished, never lost); consumer groups' `_consume`
loops block waiting for messages that aren't arriving. Once Kafka recovers, the publisher drains
the backlog and every consumer group resumes from its last committed offset — proven by
`test_aggregator_consumer_resumes_from_committed_offset_after_restart`. A topic accidentally
deleted or its retention exhausted: no data loss (Postgres still has everything per ADR-001), but
any consumer group's committed offset becomes meaningless against the recreated topic — recovery
requires resetting that group's offset (operationally: delete and let it resume from `earliest`),
a known manual step, not automated as of U3.
