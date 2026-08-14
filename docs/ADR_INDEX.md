# ADR Index — Required Records

Each of these must exist as its own file under `docs/adr/` before Phase 9 (v2) is considered done. Use `ADR_TEMPLATE.md` for the format. Write each ADR at the point in the build where the decision is actually made — not retroactively at the end from memory.

- [x] ADR-001: PostgreSQL as source of truth
- [x] ADR-002: Kafka for event distribution (not source of truth)
- [x] ADR-003: Transactional outbox pattern
- [x] ADR-004: At-least-once delivery + idempotent processing (effective exactly-once outcome)
- [x] ADR-005: Idempotency key design and enforcement
- [ ] ADR-006: Redis as policy cache (ephemeral, not authoritative)
- [ ] ADR-007: Policy distribution — eventual consistency + reconciliation loop
- [ ] ADR-008: WebSocket fan-out architecture (Redis pub/sub across gateway instances)
- [ ] ADR-009: Multi-tenant isolation via Postgres RLS + app-layer scoping
- [ ] ADR-010: Event table partitioning strategy and retention window
- [ ] ADR-011: Object storage for large payloads, threshold and content-addressing
- [ ] ADR-012: Read replica introduction criteria (benchmark-triggered, not speculative)
- [ ] ADR-013: Failure semantics — fail-open vs fail-closed decisions per component (SDK-to-interceptor, Redis-down, approval-timeout)
- [x] ADR-014: Kafka partitioning key (trace_id vs agent_id) and ordering guarantees
- [ ] ADR-015: Circuit breaker thresholds and scope (per-tool vs per-host)
- [ ] ADR-016: Optimistic concurrency for policy edits (version check vs alternative)

Add new ADRs as new non-obvious decisions get made during the build — this list is the required minimum, not a ceiling.
