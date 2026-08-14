# Bastion — v2 Upgrade Build Plan

This assumes v1 (PRD.md/ARCHITECTURE.md/BUILD_PLAN.md Phases 0–9) is already built and its milestone tests pass. This plan upgrades that working system to production-grade correctness and a real control-plane frontend. Read UPGRADE_ARCHITECTURE.md and FRONTEND_V2.md in full before starting.

**Rule:** every phase below ends with a passing test that proves the specific guarantee, plus the corresponding ADR(s) written. A phase without both is not done.

## Phase U1 — Explicit state machine
- Implement the call lifecycle (UPGRADE_ARCHITECTURE.md §2) as an actual state machine module — not scattered conditionals. Every existing code path that transitions a call's status must go through it.
- Enforce invariants as guards inside the state machine (illegal transitions raise, don't silently no-op).
- **Milestone test:** attempt every illegal transition (e.g. `BLOCKED → EXECUTING`) and assert it's rejected; attempt every legal path and assert it succeeds; assert a `TERMINAL` state rejects any further transition.

## Phase U2 — Idempotency
- Add `idempotency_key` to the `/intercept` request contract (API_SPEC.md needs updating) and a unique-constrained table/column to enforce it at the DB level.
- SDK generates and reuses the key correctly across retries of the same logical call.
- Interceptor short-circuits repeated keys to the stored decision/result instead of re-evaluating.
- **Milestone test:** fire the same call with the same idempotency key 5 times concurrently; assert the downstream side effect happened exactly once and all 5 callers got the same result.
- Write ADR-005 (and ADR-004 jointly, since they're linked).

## Phase U3 — Transactional outbox + Kafka
- Add `outbox_events` table; every write to `events` that needs distribution also writes an outbox row in the same transaction.
- Build the outbox publisher process (polls unpublished rows, publishes to Kafka topic `tool-events`, marks published).
- Partition `tool-events` by `trace_id`; document the ordering guarantee/non-guarantee explicitly in code comments and UPGRADE_ARCHITECTURE.md if it changes from the current draft.
- Migrate the Aggregator (previously reading Postgres LISTEN/NOTIFY per v1 ARCHITECTURE.md) to consume from Kafka instead. Keep Postgres as source of truth — Aggregator's job becomes "build read-model from Kafka stream," not "be the only consumer of truth."
- Add two more consumer groups (`analytics`, `security`) even if their downstream logic is a stub for now — prove the fan-out pattern works with multiple independent groups.
- **Milestone test:** kill the outbox publisher mid-batch, restart it, assert no event is lost and none is duplicated in Kafka beyond at-least-once (downstream fold must already be idempotent per U1). Kill the aggregator, restart, assert it resumes from committed offset and rebuilds identical state. Spin up a fresh analytics consumer from `--from-beginning` and assert it reprocesses full history correctly.
- Write ADR-001, ADR-002, ADR-003, ADR-014.

## Phase U4 — Optimistic concurrency on policies
- Add `version` check to the policy update query (UPGRADE_ARCHITECTURE.md §5); return `409 Conflict` on mismatch.
- Update the Policy Studio UI (see Phase U9) to handle 409 by re-fetching and prompting the user to reconcile.
- **Milestone test:** two concurrent updates to the same policy from stale versions — assert exactly one succeeds and the other gets a clean 409, not a silent overwrite.
- Write ADR-016.

## Phase U5 — Policy distribution correctness
- Redis pub/sub broadcast on policy version change (already partially in v1 — extend with the reconciliation loop).
- Each interceptor runs a periodic self-check against Postgres for any policy it's missing an update notification for.
- **Milestone test:** simulate an interceptor missing a pub/sub message (e.g. disconnect it briefly during the broadcast), assert it still converges to the correct policy version within the reconciliation interval.
- Write ADR-007.

## Phase U6 — Circuit breakers + multi-dimensional rate limiting / cost governance
- Implement the three-state breaker per `(agent_id, tool_name)` in the interceptor's execution path.
- Extend the policy DSL with a `limits:` block (per-agent, per-tool, per-org spend, per-transaction cap, LLM/tool-call/runtime budgets per UPGRADE_ARCHITECTURE.md §8).
- **Milestone test:** force a downstream tool to fail repeatedly, assert the breaker opens and subsequent calls fail fast without hitting the downstream; assert it half-opens after timeout and closes again on a successful probe. Separately, assert a policy with a `$100/transaction` cap correctly blocks a $150 call and allows a $50 one.
- Write ADR-015.

## Phase U7 — Security subsystem extension
- Add personal API tokens as a third credential type alongside JWT and agent API keys (AUTH.md needs a short addendum, not a rewrite).
- Implement the explicit `Subject → Role → Resource → Action → Policy` authorization chain, reusing the policy evaluator from the tool-call engine for both tool-call decisions and human-authorization decisions — one evaluator, two rule sets, not two implementations.
- **Milestone test:** an authorization decision (e.g. "can approver X approve this $250 payment for agent Y") is fully traceable through the same evaluation-chain shape as a tool-call decision, with a "Why?" explanation available for both.

## Phase U8 — Tenant isolation at the database layer
- Add Postgres Row-Level Security policies on every multi-tenant table (`agents`, `policies`, `traces`, `events`, etc.), keyed on `org_id` from the session context.
- **Milestone test:** with RLS enabled, attempt a cross-org read using org A's session context targeting org B's data — assert it returns nothing, even if the application-layer `WHERE org_id` filter is deliberately removed in the test to prove the DB layer alone enforces isolation.
- Write ADR-009.

## Phase U9 — Database evolution: partitioning, retention, object storage
- Partition `events` by month (`events_2026_08`, etc.); add the retention/archival job (hot window → archive to object storage → detach old partitions).
- Add object storage integration: payloads above the size threshold get offloaded, `events.payload` stores a pointer object (§12 in UPGRADE_ARCHITECTURE.md).
- Add connection pooling and query timeouts.
- **Milestone test:** insert events spanning 3 synthetic months, assert queries correctly hit only relevant partitions (check query plan); assert a large payload round-trips correctly through object storage via its pointer.
- Write ADR-010, ADR-011.

## Phase U10 — Read replica (only if justified)
- Run the Phase U13 load test first. If and only if it shows primary saturation under realistic read load, add a read replica and route trace/replay/analytics reads to it.
- **Milestone test:** before/after load test numbers showing the primary's saturation point and the improvement from replica routing. If the numbers don't justify it, write the ADR explaining why you did NOT add a replica — that's a legitimate and stronger outcome than adding one speculatively.
- Write ADR-012.

## Phase U11 — Realtime fan-out at scale
- Introduce a second WebSocket gateway instance; wire Aggregator → Redis pub/sub → both gateways, keyed by `org_id`/`agent_id`.
- Add batching/backpressure: coalesce events into a bounded window (e.g. 100ms) before pushing to WebSocket clients, tunable.
- **Milestone test:** client A connects to gateway 1, client B to gateway 2, an event originates and is delivered correctly to both. Separately, flood the system with a burst of events and assert the dashboard doesn't fall behind or crash — measure actual propagation latency under the burst.
- Write ADR-008.

## Phase U12 — Observability
- OpenTelemetry instrumentation across the full path (Agent SDK → Interceptor → Policy → Postgres → Kafka → Aggregator → WebSocket → Browser), using a distinctly-named OTel trace concept from the agent-execution `trace_id` already in DATA_MODEL.md — do not collide the two.
- RED metrics, USE metrics, and business metrics (UPGRADE_ARCHITECTURE.md §14) exported to Prometheus; Grafana dashboards checked into `infra/grafana/dashboards/` as JSON.
- **Milestone test:** pick one real request, follow its OTel trace end to end in Grafana/Jaeger and confirm every hop is visible with correct timing.

## Phase U13 — SLOs, load testing, alerting
- Define the SLO table from UPGRADE_ARCHITECTURE.md §15 as actual Prometheus alerting rules.
- Run k6 load tests at 50/100/500/1K/5K RPS; record p50/p95/p99, error rate, CPU, memory, DB connections, Kafka lag at each level.
- **Milestone deliverable:** the real numbers table in the README, plus identification of the bottleneck where latency inflects (not just a max-RPS claim).

## Phase U14 — Chaos testing
- Build the chaos test suite from UPGRADE_ARCHITECTURE.md §16 — each fault injected with its asserted invariant, automated (not manual clicking), runnable in CI or on demand.
- **Milestone:** full chaos suite passing, results documented in `docs/CHAOS_RESULTS.md` with what was tested, what broke on the first attempt (there will be something — document the fix, that's the credible version of this exercise), and what the final passing state proves.

## Phase U15 — Frontend v2
Build in the order given in FRONTEND_V2.md — flagship experiences first, each wired to the real backend mechanism it depends on:
1. Live Execution Graph upgrade (state-machine-aware coloring, synchronized timeline, real WS fan-out from U11)
2. Policy Studio (visual builder over the real DSL, simulator hitting the real evaluator, version diff using U4's versioning, real propagation status from interceptor health)
3. Incident Replay (derived purely from the event log — if it can't be, that's a backend gap, fix it there)
4. Command Center, Trace Explorer, Approval Center, Threat Center, Agent Detail/Health, Cost Center, command palette, shared "Why?" component
- **Milestone test:** for each flagship screen, one test/checklist item proving it reflects real backend state changes live (e.g. change a policy via API, confirm the Policy Studio propagation indicator updates without a page refresh).

## Phase U16 — ADRs and final docs
- Complete every item in `docs/adr/ADR_INDEX.md`.
- Update README.md, API_SPEC.md, SETUP.md with everything added in v2.
- Write `docs/CHAOS_RESULTS.md` and the load-test results table if not already placed in the README.

## Ordering notes
- U1–U4 are foundational correctness and block almost everything else — do them first, in order.
- U5–U8 can be parallelized in reasoning but should still be built sequentially by a single agent session to avoid half-finished cross-cutting changes.
- U9–U11 are the scale/operability layer — do after correctness is solid.
- U12–U14 are the proof layer — they validate everything before them and should not be rushed or skipped to save time.
- U15 (frontend) intentionally comes last, same rule as v1: don't build the impressive UI on top of unproven guarantees.
