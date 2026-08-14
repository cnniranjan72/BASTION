# Bastion — Architecture v2 (Production-Grade Upgrade)

This document supersedes parts of ARCHITECTURE.md where they conflict. v1 proved the concept works end to end. v2 makes it correct under concurrency, failure, and scale. Read ARCHITECTURE.md and DATA_MODEL.md first — this is a delta on top of them, not a replacement.

## 1. Why this upgrade exists
v1 answers "does the control plane work?" v2 answers the questions a senior engineer actually asks in review:
- What happens if Bastion says "allowed" and then crashes before the agent executes?
- What happens if the agent executes but the completion event never arrives?
- What happens if two admins edit the same policy at once?
- What happens if Kafka, Redis, or Postgres goes down independently?
- What happens at 5,000 requests/sec instead of 50?

Every section below exists to answer one of these questions with an explicit, testable guarantee — not a hand-wave.

## 2. The call lifecycle state machine
Every intercepted call is an instance of this state machine. This is the core correctness artifact of the whole system — implement it as an actual explicit state machine (not implicit if/else chains scattered across the codebase).

```
CREATED
   │
   ▼
ATTEMPTED
   │
   ├── BLOCKED ────────────────────► TERMINAL
   │
   ├── PENDING_APPROVAL
   │        │
   │     APPROVED ─────┐
   │        │          │
   │     DENIED ────────────────────► TERMINAL
   │                   │
   │                   ▼
   └───────────────► ALLOWED
                        │
                        ▼
                    EXECUTING
                        │
                ┌───────┴───────┐
                ▼               ▼
            COMPLETED         FAILED
                │               │
                └───────┬───────┘
                        ▼
                    TERMINAL
```

### Invariants (write these as tests, not comments)
1. A `BLOCKED` call can never transition to `EXECUTING`.
2. A `TERMINAL` state never transitions to any other state — terminal is terminal.
3. Every state transition is recorded as exactly one immutable event with a unique `event_id`.
4. Every tool invocation carries an idempotency key; replays of the same key never re-execute the underlying action twice.
5. `PENDING_APPROVAL` has a hard timeout after which it deterministically transitions to `DENIED` (fail-closed default, matches AUTH.md's security posture).

## 3. Idempotency (the single biggest correctness gap in v1)
### The problem
```
Agent → POST /intercept → ALLOWED → network timeout → Agent retries → POST /intercept again
```
Did the original call execute or not? Without an idempotency key, a retried payment charge, a retried DB delete, or a retried email send can happen twice.

### The mechanism
- Idempotency key = `(agent_id, trace_id, span_id, idempotency_key)` — the SDK generates and attaches this on the *first* attempt and reuses it on retries of the *same logical call*.
- The interceptor checks: has this exact key already reached a terminal or in-flight state? If yes, return the existing decision/result instead of re-evaluating and re-executing.
- Store `(idempotency_key) → (span_id, status, result)` in Postgres with a unique constraint — the DB enforces this, not just application logic.
- This applies with extra weight to: payments, database mutations, email/notification sends, and any external API call with side effects. Read-only tool calls don't strictly need it but get it for free from the same mechanism.

### Interview framing
This is your exactly-once vs at-least-once discussion. Be precise: Bastion delivers **at-least-once delivery** of the underlying tool call attempt, combined with **idempotent execution** at the interceptor layer, which together produce an effective **exactly-once outcome** for the caller — without requiring the downstream API itself to be idempotent.

## 4. Kafka — properly, not decoratively
Don't wire `Postgres → Kafka` and call it done. Two specific problems must be solved explicitly.

### 4.1 The dual-write problem → Transactional Outbox
Naively: write the audit event to Postgres, then publish to Kafka as a second step. If the process crashes between the two, the DB and the event bus disagree forever.

Fix — write both in one transaction:
```
BEGIN
  INSERT INTO events (...)
  INSERT INTO outbox_events (...)
COMMIT
```
A separate **outbox publisher** process polls `outbox_events` for unpublished rows, publishes to Kafka, then marks them published (or deletes them). If the publisher crashes mid-batch, it resumes from the last unpublished row — no event is ever lost, and none is committed to Postgres without eventually reaching Kafka.

### 4.2 Source of truth vs distribution
- **PostgreSQL = durable source of truth.** If Kafka disappears entirely, no event is lost — it's still in `events`/`outbox_events`, and the outbox publisher catches up when Kafka returns.
- **Kafka = event distribution/fan-out**, not the ledger. Aggregator, analytics, and security consumers all read from Kafka, but none of them are the system of record.
- **Redis = ephemeral acceleration only** (policy cache, rate-limit counters). If Redis disappears, policy cache reloads from Postgres and rate limits reset — degraded, not broken.

### 4.3 Partitioning and ordering
- Kafka topic `tool-events`, partitioned by `trace_id` (or `agent_id` if trace-level partitioning creates hot partitions for very long-running agents — measure before deciding).
- **Explicit guarantee:** events belonging to the same causal execution (same partition key) are ordered within their partition. **Explicit non-guarantee:** there is no global ordering across traces/partitions. State this plainly in the docs and in interviews — pretending distributed systems have global ordering is the wrong answer, not the impressive one.

### 4.4 Consumer groups + replay
- At least three independent consumer groups on `tool-events`: `aggregator`, `analytics`, `security`. Each gets the full stream independently — that's the point of consumer groups over a single queue.
- Prove resumability: kill the aggregator mid-stream, restart it, verify it resumes from its committed offset and rebuilds identical state (no loss, no duplication of *effect* — duplication of delivery is fine because state rebuild is idempotent, i.e. folding the same event twice produces the same result).
- Prove replay: spin up a brand-new analytics consumer with `--from-beginning`, verify it reprocesses full history correctly.

## 5. Optimistic concurrency on policies
Two admins editing the same policy concurrently must not silently clobber each other.
```sql
UPDATE policies
SET definition = $1, version = version + 1, updated_at = now()
WHERE id = $2 AND version = $3   -- $3 = version the admin last read
```
Zero rows affected → return `409 Conflict` to the client, force a re-fetch-and-retry. This is a small feature with outsized system-design credibility — implement it exactly, don't skip it.

## 6. Policy distribution as a real distributed system
- Every interceptor instance holds `policy_id → version` in memory.
- On policy update: Postgres write → Redis pub/sub broadcast of `{policy_id, new_version}` → each interceptor invalidates and re-fetches that policy, atomically swapping its in-memory cache entry (never serve a half-updated policy).
- **Failure case to handle explicitly:** what if an interceptor misses the pub/sub message (e.g. it was down, or the message was dropped)? Add a periodic reconciliation loop (every N seconds, each interceptor checks its cached versions against Postgres and self-heals). This gives you **eventual convergence** as an explicit, provable guarantee rather than an assumption.

## 7. Circuit breakers on downstream tool calls
Bastion doesn't just govern agents — it protects the systems agents call. Implement a standard three-state breaker per `(agent_id, tool_name)` or per downstream host:
```
CLOSED → (failure threshold exceeded) → OPEN → (timeout elapses) → HALF_OPEN → (success) → CLOSED
                                                                   → (failure) → OPEN
```
Lives in the interceptor's execution path, right before the real downstream call is made (after policy evaluation has already allowed it).

## 8. Multi-dimensional rate limiting and cost governance
Move beyond a single global "requests/min." Support limits composed across dimensions in the policy DSL:
- Per agent (`100 calls/min`)
- Per tool (`payments.transfer: 10 calls/min`)
- Per organization spend (`$5,000/day`)
- Per single transaction (`$100 max auto-approved`)
- Per agent LLM budget (`$5/hour`), tool-call budget, and runtime budget

This is the extension of the policy engine from "traffic control" to "behavioral and financial governance" — it's what makes Bastion a governance layer, not just a gateway. Extend the policy DSL (see ARCHITECTURE.md §2.3) with a `limits:` block alongside `match:`/`action:`.

## 9. Security subsystem, properly layered
Extends AUTH.md, doesn't replace it.
- **Authentication:** JWT (human users), personal API tokens (for scripts/CI), agent API keys (machine-to-machine) — three distinct credential types, each with its own hashing/rotation/expiry rules.
- **Authorization model:** explicit `Subject → Role → Resource → Action → Policy` evaluation chain, so "can approver X approve a $250 payment for agent Y" is answerable as a single traceable evaluation, same shape as the tool-call policy engine itself. Reuse the policy engine's evaluation mechanics for both — one evaluator, two rule sets.

## 10. Tenant isolation at the database layer
`org_id` scoping at the application layer (already in DATA_MODEL.md) is necessary but not sufficient — a single missed `WHERE org_id = ...` is a data leak. Add **Postgres Row-Level Security (RLS)** policies on every multi-tenant table so isolation is enforced by the database itself, not solely trusted to application code. Document this explicitly as a design decision (this is ADR material — see §14).

## 11. Database evolution
- Add a read replica once write-heavy load testing (see §12) shows primary saturation — don't add it speculatively. Benchmark first, then justify.
- Route trace/replay/analytics reads to the replica; keep writes and any read that must be strongly consistent (e.g. approval resolution) on the primary.
- Connection pooling (PgBouncer or equivalent), query timeouts, and explicit index list (already started in DATA_MODEL.md, extend it here).
- **Partition `events` by time** (`events_2026_08`, `events_2026_09`, ...) — this table grows forever, plan for it now rather than retrofitting under production load.
- **Retention + archival:** hot events live in Postgres partitions; older partitions get archived to object storage and detached. Define the retention window explicitly (e.g. 90 days hot, then archive) — pick a number and defend it.

## 12. Object storage for large payloads
Tool call payloads (large tool responses, documents an agent read, etc.) do not belong in Postgres rows. Store:
- In `events.payload`: a small pointer object `{ "storage": "s3", "uri": "...", "hash": "...", "size_bytes": ... }`
- In object storage: the actual large payload, content-addressed by hash where practical (dedup for identical payloads).
Small structured data (amounts, tool names, decisions) stays inline in Postgres; anything above a size threshold (e.g. 8KB) gets offloaded.

## 13. Realtime fan-out at scale
v1's WebSocket design (aggregator → single WS server → browser) breaks the moment there's more than one WS server: client A connects to server 1, client B to server 2, an event arrives at server 1 — how does B find out?

Fix:
```
Kafka → Aggregator → Redis Pub/Sub → [WS Gateway 1, WS Gateway 2, ...] → respective connected clients
```
Any WS gateway instance can serve any client because they all subscribe to the same Redis pub/sub channels, keyed by `org_id`/`agent_id`. This is the standard fan-out pattern for horizontally scaled realtime systems — implement it as such, don't special-case it.

### Backpressure
An agent producing 100,000 events/sec into a dashboard that can render 1,000/sec must not fall over. Kafka provides the buffer; the aggregator batches events before pushing to WebSocket (e.g. coalesce updates within a 100ms window into a single message per affected node) rather than pushing one WS message per event. Document the batching window as a tunable, and measure the tradeoff between latency and message volume.

## 14. Observability, seriously
- **OpenTelemetry** end to end: one `trace_id` followed across Agent SDK → Interceptor → Policy evaluation → Postgres write → Kafka publish → Aggregator → WebSocket → Browser. This is a genuinely different (and harder) trace than the agent-execution `trace_id` in DATA_MODEL.md — be explicit in code and docs about which "trace" you mean in which context (agent execution trace vs. OTel infrastructure trace); don't let the two concepts collide under one name.
- **RED metrics** (Rate, Errors, Duration) per service: `intercept_requests_total`, `intercept_errors_total`, `intercept_latency_ms` (histogram, not just average).
- **USE metrics** (Utilization, Saturation, Errors) for infra: Kafka consumer lag, Redis latency, Postgres connection pool saturation.
- **Business metrics** as first-class: `calls_allowed_total`, `calls_blocked_total`, `calls_pending_total`, `approval_resolution_seconds`, `estimated_cost_total`, `policy_violation_rate`.
- Prometheus + Grafana for all of the above; dashboards checked into the repo as code (`infra/grafana/dashboards/*.json`), not click-configured and undocumented.

## 15. SLOs (define, measure, alert — in that order)
| SLO | Target |
|---|---|
| Availability (interceptor) | 99.9% |
| `/intercept` latency | p99 < 50ms |
| Policy decision (in-process, excluding network) | p99 < 10ms |
| Event durability | 99.99% (no event lost once ack'd) |
| WebSocket propagation (event write → client receipt) | p99 < 500ms |

Alerting rule example: `intercept p99 > 50ms sustained for 5 minutes → page`. The chain `requirement → architecture → implementation → measurement → alerting` is the actual system-design deliverable — a target with no measurement behind it is a claim, not an SLO.

## 16. Chaos and load testing as first-class deliverables
### Chaos scenarios (each must have an asserted, passing invariant)
| Fault injected | Required invariant |
|---|---|
| Kill interceptor mid-request | client sees a clean error or a fully-completed idempotent retry — never a half-applied state |
| Kill aggregator | zero event loss (Kafka retains, aggregator resumes from offset on restart) |
| Kill Kafka | audit trail still persists (outbox + Postgres), publisher catches up when Kafka returns |
| Kill Redis | policy cache falls back to Postgres fetch; rate limits reset safely (fail open or closed — pick one, document why) |
| Postgres +500ms latency injected | interceptor p99 degrades predictably, does not deadlock or cascade-fail |
| Drop WebSocket connection mid-session | client reconnects and correctly resyncs current graph state (not just future deltas) |
| Duplicate a Kafka event | downstream fold is idempotent — duplicate has zero effect on derived state |
| Reorder events within a partition (simulate) | document what breaks, if anything, and why partition-key ordering is assumed to prevent this in practice |

### Load testing (k6 or locust)
Run at 50 / 100 / 500 / 1K / 5K RPS. At each level record p50/p95/p99 latency, error rate, CPU, memory, DB connection count, Kafka consumer lag. Publish the actual table in the README. "50 RPS → 22ms p99, 500 RPS → 41ms p99, 1K RPS → 83ms p99" plus an explanation of the bottleneck at the point latency inflects is far stronger evidence than an unqualified "supports 10K RPS" claim.

## 17. Architecture Decision Records
Every decision in this document becomes an ADR under `docs/adr/`. See `ADR_INDEX.md` for the required list and `ADR_TEMPLATE.md` for the format (Context → Options → Decision → Consequences → Failure modes). This is not optional documentation busywork — the ADRs are the artifact that turns "I used Kafka" into "I can defend why I used Kafka over the alternatives, and what breaks if the choice is wrong."

## 18. Final v2 stack (deliberately not 30 technologies)
| Layer | Technology |
|---|---|
| API | FastAPI |
| SDK | Python |
| Database | PostgreSQL (+ partitioning, pooling, RLS, read replica once justified) |
| Cache | Redis |
| Event bus | Kafka |
| Reliability | Transactional outbox + idempotency keys |
| Realtime | WebSockets + Redis pub/sub fan-out |
| Object storage | S3-compatible |
| Observability | OpenTelemetry + Prometheus + Grafana |
| Containers | Docker |
| Orchestration | Kubernetes |
| Load testing | k6 |
| Security | JWT + API keys + RBAC + Postgres RLS |
| CI/CD | GitHub Actions |
| Policy engine | Custom (extended DSL with limits/cost governance) |

No new technology gets added beyond this list unless it's solving a demonstrated problem surfaced by load testing or chaos testing — not "because it would look good."
