# ADR-008: WebSocket fan-out architecture (Redis pub/sub across gateway instances)

## Status
Accepted

## Context
UPGRADE_ARCHITECTURE.md §13 names v1's actual scaling gap directly: "v1's WebSocket design
(aggregator → single WS server → browser) breaks the moment there's more than one WS server: client A
connects to server 1, client B to server 2, an event arrives at server 1 — how does B find out?"
`ws.py`'s `ConnectionManager` was exactly that — a bare in-process `dict[agent_id, set[WebSocket]]`,
broadcast by directly iterating local connections. Correct for exactly one process, silently wrong the
moment a second one exists (client B simply never receives anything, no error, no signal anything's
missing).

## Options considered
1. **Kafka → Aggregator → Redis Pub/Sub → every WS gateway instance** (chosen), exactly as §13
   specifies. Every gateway instance publishes and subscribes through the same per-agent Redis
   channel — delivery to a locally-connected client always happens via that subscription, never as a
   direct side effect of the Kafka-consumer callback that produced the message. This makes "any
   gateway instance can serve any client" a property of the code path itself, not something that only
   happens to work when there's one process.
2. **Sticky sessions / consistent hashing at the load balancer** (route a given `agent_id`'s clients to
   the same backend instance every time). Rejected: doesn't solve the actual problem (an event
   still needs to reach whichever instance a client is stuck to), just relocates where the fan-out
   gap would reappear, and adds real operational complexity (load balancer configuration, instance
   affinity, rebalancing on scale-events) for no benefit over pub/sub.
3. **A dedicated message broker for this (e.g. a Kafka topic) instead of Redis.** Rejected: Redis
   pub/sub is already the established pattern for this exact "publish a small message, fan out to
   every subscriber, no durability needed" shape elsewhere in this system (U5's policy hot-reload,
   the approval wake-up signal) — reusing it keeps the "ephemeral, non-authoritative" role Redis
   already plays consistent, rather than introducing a second broker technology for the same kind of
   problem Kafka already solves for the *durable*, at-least-once distribution case (ADR-002).

## Decision
`aggregator/redis_bus.py` (new, mirrors the interceptor's own — same RESP2-pinning fix, same shape)
publishes/subscribes on `bastion:ws:{agent_id}`. `ws.py`'s `ConnectionManager.broadcast()` now only
publishes; `_subscribe_loop` (one asyncio task per `agent_id` with at least one locally-connected
client, started on first connect, cancelled on last disconnect) is the *only* code path that ever
calls `_deliver_locally`. Proven directly, not just designed for: two genuinely independent
`ConnectionManager` instances (no shared Python state, only the real Redis instance connecting them —
the same "independent instances, real shared infrastructure" pattern already used for U3's Kafka
multi-consumer proofs) — a broadcast published through one is received by a client connected only to
the other (`test_broadcast_from_one_gateway_reaches_clients_on_both`).

**Backpressure** (§13's own example: "an agent producing 100,000 events/sec into a dashboard that can
render 1,000/sec"): `_enqueue` coalesces multiple `NodeUpdatedMessage`s for the same `span_id`
arriving within `batch_window_seconds` (default 100ms, `config.ws_batch_window_seconds`, tunable) into
just the latest one. `NodeAddedMessage`/`EdgeAddedMessage` are structural facts, never coalesced —
only a node's *current status* is ever safe to collapse to "whatever it is by the time the window
flushes." `batch_window_seconds=0` disables coalescing entirely (immediate, one message per update) —
what the three exact-sequence WS tests predating U11 now explicitly opt into via a
`_no_coalescing()` helper, since the default window genuinely can (and, once, did — caught directly,
not assumed) coalesce two rapid updates to the same span in `test_two_viewers_see_identical_live_updates_with_no_polling`'s
own real timing.

## Consequences
- The wire format is unchanged — individual JSON messages, one per `send_json()` call, same as v1 —
  coalescing reduces message *count* during a burst, not message *shape*; no existing consumer
  (frontend or otherwise) needs to change how it parses a message.
- A burst of 200 rapid updates to one node delivers as far fewer than 200 messages while still
  landing on the correct final state, with propagation latency for that final state bounded by
  roughly the coalescing window rather than growing with burst size — proven directly
  (`test_burst_of_rapid_updates_coalesces_and_measures_propagation_latency`), the actual "doesn't
  fall behind" claim this phase's milestone test asks for.
- Every gateway instance now needs a live Redis connection to serve WS traffic at all (previously,
  Redis wasn't used by the aggregator/gateway role at all) — a new hard dependency on the WS-serving
  path, not yet given a documented graceful-degradation story if Redis is unreachable (see Failure
  modes).

## Failure modes
Redis unreachable when a client tries to connect: `_subscribe_loop`'s `redis_bus.subscribe_live_messages`
call would raise, caught by the `except Exception: log.exception(...)` in `_subscribe_loop` — the
WS connection itself stays open (the client doesn't get disconnected), but silently never receives
anything, since the subscription that would feed it never establishes. Not yet surfaced to the client
in any way (no error message, no reconnect-prompting close code) — a real, currently-undocumented gap,
distinct from U6's Redis-failure handling (which fails open on a *decision* path); here there's no
"open" fallback available, since live delivery has no other transport. Redis unreachable mid-session
(a connection that was working, then isn't): the same silent-stall outcome — `pubsub.listen()`'s
internal reconnect behavior is whatever `redis.asyncio` does by default, not hardened or tested here.
Flagged as a real follow-up rather than assumed handled.
