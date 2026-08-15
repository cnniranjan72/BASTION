# Chaos test results — U14 (v2 upgrade)

Source of the 8-scenario table this phase implements:
`UPGRADE_ARCHITECTURE.md` §16. Build-plan milestone
(`UPGRADE_BUILD_PLAN.md`'s U14 entry): "full chaos suite passing, results
documented ... with what was tested, what broke on the first attempt ...
and what the final passing state proves."

## Methodology

Every fault below is injected at the precise mechanism boundary a real
crash/outage would produce, rather than via a raw OS-level process
`SIGKILL` raced against arbitrary network I/O timing. A real kill can land
at any instruction; racing one non-deterministically would make these
tests flaky and, worse, would let them pass by accident without ever
reliably exercising the boundary that actually matters. Instead:

- "Kill interceptor mid-request" reproduces, deterministically, the exact
  Postgres state a process death between reserving and completing an
  idempotency key would leave — by calling the same DB mutation the real
  code path calls, directly.
- "Kill Kafka" / "Kill Redis" use real `docker stop` / `docker start`
  against the actual `bastion-kafka` / `bastion-redis` containers this
  test session's own live services (OutboxPublisher, KafkaEventConsumer,
  Redis pub/sub) are already running against — genuine outages, not
  simulated ones.
- "Postgres +500ms latency" injects real, measured `asyncio.sleep(0.5)`
  delay into the interceptor's actual live asyncpg pool for the duration
  of one test, via a proxy object (see finding below) — real added
  latency, not fabricated timing numbers.
- "Drop WebSocket / reconnect" and "Duplicate a Kafka event" exercise the
  real WS and Kafka-consumer code paths directly.
- "Reorder events" is the one scenario the architecture doc itself only
  asks to be simulated (Kafka's own partition-key guarantee makes it
  unreproducible for real) — see its section below.

All 9 tests live in `aggregator/tests/chaos/` and run as part of the
ordinary workspace pytest suite (`uv run pytest aggregator/tests/chaos/`
in isolation, or as part of the full run) — automated, not manual
clicking, and runnable in CI exactly like every other test here.

## Scenario 1 — Kill interceptor mid-request

**Invariant:** client sees a clean error or a fully-completed idempotent
retry — never a half-applied state.

**Test:** `test_chaos_interceptor_crash_idempotency.py`

**What broke on the first real attempt investigated (not the test run
itself — the *design* investigation before writing it):** tracing through
`_intercept()` (interceptor/src/bastion_interceptor/main.py) revealed that
a process killed *after* `try_reserve_idempotency_key` succeeds but
*before* `complete_idempotency_key` runs leaves an orphaned reservation
row. A retry against it doesn't self-heal: `_await_idempotent_result`
polls for 2 seconds, finds no completion, and the caller raises a clean
503 `IDEMPOTENT_REQUEST_IN_PROGRESS` — technically satisfying the
invariant (a clean error, never a half-applied state) — but **every
subsequent retry gets the identical 503, forever**. No reaper/expiry
mechanism exists for stuck `idempotency_keys` rows. This is a real,
accepted limitation, left as-is this phase (not required by the stated
invariant, and building a reaper is real scope beyond a chaos test's
remit) — noted here rather than silently discovered and ignored.

**Final passing state proves:** an interceptor process death between
reserving and completing an idempotency key never produces a duplicate
decision, a corrupted event, or a hang — only a clean, well-formed error.

## Scenario 2 — Kill aggregator

**Invariant:** zero event loss (Kafka retains, aggregator resumes from
offset on restart).

**Test:** already covered — `aggregator/tests/test_kafka_resumability.py::test_aggregator_consumer_resumes_from_committed_offset_after_restart`,
written during U3. Not duplicated here: that test's own docstring already
frames it as exactly this scenario ("Kill the aggregator, restart, assert
it resumes from committed offset and rebuilds identical state"), and it
continues to pass as part of every full-suite run, including the one this
phase's changes were verified against.

## Scenario 3 — Kill Kafka

**Invariant:** audit trail still persists (outbox + Postgres), publisher
catches up when Kafka returns.

**Test:** `test_chaos_kafka_outage.py`

**Result:** passed on the first real run. `POST /intercept` succeeds
identically with `bastion-kafka` stopped (the transactional outbox writes
to Postgres in the same transaction as the decision — Kafka is never in
that path), the event and its outbox row are confirmed present and
unpublished, and once the container is restarted the same
already-running `OutboxPublisher` (not a fresh one spun up for the test)
drains the backlog within 30 seconds without any code change or manual
intervention.

## Scenario 4 — Kill Redis

**Invariant (as written in UPGRADE_ARCHITECTURE.md §16):** "policy cache
falls back to Postgres fetch; rate limits reset safely (fail open or
closed — pick one, document why)."

**Doc/code conflict found and resolved** (flagged per the standing rule
rather than silently reinterpreted): "policy cache falls back to Postgres
fetch" doesn't match this system's actual, already-decided design.
`PolicyCache` (interceptor/src/bastion_interceptor/policy.py) is a plain
in-memory dict with **no** Redis dependency for reads — a Redis outage
doesn't affect it either way — and a genuine cache miss has never
performed a synchronous Postgres fetch; it defaults to
`Decision(action="allow")` (`policy.py:171-172`, "safe default"). A
synchronous per-request Postgres fetch on a cache miss would itself
violate CLAUDE.md rule #4 ("`/intercept` never blocks on non-essential
work") — U5's real design (**ADR-007**) deliberately chose async periodic
reconciliation (`policy_reconciler.py`, a 30-second full sweep,
Redis-independent since it reads Postgres directly) specifically to avoid
that. **Resolution:** this test verifies the system's real, already-decided
behavior — cache reads are Redis-independent, and a miss defaults to
allow rather than a Postgres round-trip — and cites ADR-007 rather than
writing a new ADR for an already-recorded decision.

The rate-limits half was already decided in **ADR-015** (U6): both
`limits.check_and_apply_limits` and `circuit_breaker.is_open` fail *open*
on `redis.RedisError`. This phase verifies that choice empirically against
a real Redis outage rather than only trusting the ADR's prose.

**Test:** `test_chaos_redis_outage.py` (two cases: an agent with a
`calls_per_minute`-limited policy, and an agent with no policy at all).
Both passed on the first real run — `/intercept` returned `200
allowed` in both cases with `bastion-redis` stopped.

## Scenario 5 — Postgres +500ms latency injected

**Invariant:** interceptor p99 degrades predictably, does not deadlock or
cascade-fail.

**Test:** `test_chaos_postgres_latency.py`

**What broke on the first attempt:** the first implementation tried to
monkeypatch `fetch`/`fetchrow`/`fetchval`/`execute` directly onto the live
`asyncpg.Pool` instance — `asyncpg.Pool` defines `__slots__`, so this
failed immediately with `AttributeError: 'Pool' object attribute 'fetch'
is read-only`. Fixed by swapping `Database._pool` itself (a plain
attribute, not slotted) for a thin proxy object for the test's duration,
restored in a `finally` block. Building the proxy surfaced a second,
easy-to-miss detail: `insert_event` — the write every `/intercept` call
makes — acquires a raw connection via `pool.acquire()` and calls
`fetchval`/`execute` on *that connection*, not on the pool object. A
proxy that only wrapped the pool's own verb methods would have silently
under-delayed the exact write this test most wanted to slow down; the
proxy's `acquire()` had to return a context manager yielding a
similarly-wrapped connection proxy.

**Result after the fix:** 5 concurrent `/intercept` calls under a real
500ms-per-query injected delay all completed successfully (`200`), each
taking between 0.5s and 5s (bounded, not runaway) — proving the
degradation is predictable rather than cascading — and a request made
immediately after removing the injected delay dropped back under 0.5s,
confirming no lingering pool corruption.

## Scenario 6 — Drop WebSocket connection mid-session

**Invariant:** client reconnects and correctly resyncs current graph
state, not just future deltas.

**Test:** `test_chaos_ws_reconnect_resync.py`

**What broke on the first attempt (a real gap, fixed as part of this
phase, not just documented):** before this phase, `WS /live/{agent_id}`
(`aggregator/src/bastion_aggregator/main.py`'s `live()` route) only ever
registered the new connection and waited for future broadcasts —
`ws.py`'s `ConnectionManager` only ever pushes forward to already-
subscribed connections. A client that dropped and reconnected saw nothing
but whatever happened *after* it reconnected; anything that happened
while it was disconnected was silently lost, with no error anywhere to
signal it.

**Fix:** `main.py` gained `_send_resync_snapshot`, called immediately
after `ws_manager.connect()` accepts the new connection. It replays the
connecting agent's `active_traces` state as the same delta message types
a live client already knows how to reduce (`node_added` [+ `edge_added`],
then `node_updated` to the real current status) — no new message type or
client-side handling needed. Ordering choice, made explicit in a code
comment: `connect()` runs before the snapshot is read, so a live event
landing in that narrow window is replayed twice (harmless duplicate)
rather than the alternative ordering's risk of silently missing it.

**Scope, stated explicitly:** only resyncs traces still *running* as of
reconnect time. A trace that reached a terminal state while disconnected
is evicted from `active_traces` (by existing, pre-U14 design) and isn't
resynced over the live channel — `GET /traces/{id}` is what a client
wants for history; the live channel's job is in-flight state.

**Result after the fix:** a client connected before any traces exist,
disconnects, a trace starts and reaches "allowed" entirely while it's
gone, and a fresh connection receives the full `node_added` +
`node_updated(allowed)` state as its first two messages — passed.

## Scenario 7 — Duplicate a Kafka event

**Invariant:** downstream fold is idempotent — duplicate has zero effect
on derived state.

**Test:** `test_chaos_duplicate_kafka_event.py`

**Result:** passed on the first real run, as expected from
`_handle_notification`'s existing design (it re-fetches and re-folds the
*entire* trace fresh from Postgres on every message rather than
incrementally applying the message's own payload — documented in
`main.py` since U3 as exactly what makes Kafka's at-least-once delivery
safe without a separate dedup step). Calling `_handle_notification` twice
with an identical notification payload produces byte-identical
`trace_summaries` rows and never double-counts `total_calls`.

## Scenario 8 — Reorder events within a partition (simulate)

**Required deliverable (this row has no "must hold" invariant):**
document what breaks, if anything, and why partition-key ordering is
assumed to prevent this in practice.

**Test:** `test_chaos_event_reorder.py`

**What breaks:** silently, not loudly. `fold_events_to_graph`
(`aggregator/src/bastion_aggregator/graph.py`) looks up `nodes.get(span_id)`
for any non-`CallAttempted` event and skips it if the node doesn't exist
yet — a comment in the code already called this "defensive: shouldn't
happen, every span starts with CallAttempted," true only under in-order
delivery. Folding the same three events (`CallAttempted`, `CallAllowed`,
`CallCompleted`) in reverse order (`CallCompleted`, `CallAllowed`,
`CallAttempted`) drops the first two entirely: the node gets created
afterward by the delayed `CallAttempted`, frozen at status `"pending"`
forever. Because the reordered event was the root span's own terminal
event, `ended_at` never gets set either — the whole trace is stuck
reporting `status: "running"` permanently, with **no exception, no log
line, nothing** to signal it happened. Confirmed directly: `test_reordered_events_silently_drop_updates_and_the_trace_never_terminates`
asserts exactly this stuck state.

**Why partition-key ordering prevents this in practice:** Kafka's
partitioning key for the `tool-events` topic is `trace_id`
(**ADR-014**), which guarantees FIFO delivery *within* a partition —
every event for a given trace is produced to, and consumed from, the same
partition in the order the interceptor wrote them to Postgres (itself
strictly ordered per-trace via `bastion_next_sequence_number`'s advisory
lock, see U2/ADR-005). This scenario is therefore not reachable through
the real pipeline at all; it is only producible by calling the fold
function directly, bypassing Kafka entirely, exactly as
UPGRADE_ARCHITECTURE.md's own wording for this row anticipates.

**Not fixed this phase:** hardening the fold to tolerate genuinely
out-of-order input (buffer-and-resort by `sequence_number`, or fail loudly
instead of silently) is real design work contingent on deciding the
partitioning guarantee is no longer trustworthy enough to lean on — a
decision this phase doesn't have a reason to make. Recorded here as an
accepted, understood risk, contingent on ADR-014's guarantee holding.

## Summary

| # | Scenario | Invariant held? | Real finding |
|---|---|---|---|
| 1 | Kill interceptor mid-request | Yes | Orphaned reservations don't self-heal (accepted limitation) |
| 2 | Kill aggregator | Yes (pre-existing U3 test) | — |
| 3 | Kill Kafka | Yes | — |
| 4 | Kill Redis | Yes | Doc/code conflict on "policy cache falls back to Postgres" — resolved, cites ADR-007 |
| 5 | Postgres +500ms latency | Yes | `asyncpg.Pool.__slots__` blocked the first monkeypatch approach; fixed via a `_pool`-swap proxy |
| 6 | Drop WS mid-session | Yes, after a fix | WS reconnect never resynced state at all before this phase — implemented `_send_resync_snapshot` |
| 7 | Duplicate Kafka event | Yes | — |
| 8 | Reorder events (simulated) | N/A (documents breakage) | Confirmed: silent, permanent "stuck running" state; relies entirely on ADR-014's partition-key guarantee |

Full chaos suite: 9/9 passing (`uv run pytest aggregator/tests/chaos/`).
Full workspace suite re-verified green after every fault-injection test
ran against real shared infrastructure (Docker Kafka/Redis stopped and
restarted mid-session) — see `docs/PROGRESS.md`'s U14 entry for the exact
pass count.
