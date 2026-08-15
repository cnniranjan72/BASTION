"""Prometheus metrics. Phase 9 (v1) added `intercept_latency_seconds` and
`policy_decisions_total{decision=}` — the two that answer "is the hot path
fast" and "what fraction of calls are blocked/approved vs. allowed."

U12 (v2 upgrade), UPGRADE_ARCHITECTURE.md §14, extends this with the
standard RED/USE/business categories:
- **RED** (Rate, Errors, Duration) for every endpoint, not just
  /intercept: `http_requests_total`/`http_request_duration_seconds`,
  recorded by main.py's request_id_middleware for every request that
  passes through it.
- **USE** (Utilization, Saturation, Errors) for the two resources this
  service's correctness actually depends on: the Postgres connection pool
  (`db_pool_size`/`db_pool_in_use` — saturation is "in_use approaching
  size") and the U3 transactional outbox's backlog
  (`bastion_outbox_unpublished_total` — a growing backlog is exactly what
  "falling behind" looks like for that subsystem).
- **Business**: `policy_decisions_total` (already existed) plus
  `bastion_call_cost_dollars_total`, summing `CallCompleted`'s `cost`
  field when present — the actual dollar activity this system exists to
  govern, not just infrastructure health.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

intercept_latency_seconds = Histogram(
    "intercept_latency_seconds",
    "POST /intercept request latency, end-to-end (includes the synchronous "
    "event write — see docs/ARCHITECTURE.md §18 for why that isn't fire-and-forget).",
)

policy_decisions_total = Counter(
    "policy_decisions_total",
    "POST /intercept decisions by outcome.",
    labelnames=["decision"],
)

# Prefixed (rather than the generic http_requests_total/db_pool_size a
# single-service dashboard might reach for) because prometheus_client's
# CollectorRegistry is process-wide, global, shared default — the
# interceptor and aggregator run as separate processes in production
# (no collision there), but this repo's own test suite imports both
# packages into one process for cross-service tests, which hit a real
# `DuplicateTimeseries` registration error the first time these were
# left unprefixed and identically named on both sides.
http_requests_total = Counter(
    "interceptor_http_requests_total",
    "RED: every request handled, by method/path/status.",
    labelnames=["method", "path", "status_code"],
)

http_request_duration_seconds = Histogram(
    "interceptor_http_request_duration_seconds",
    "RED: request duration by method/path.",
    labelnames=["method", "path"],
)

db_pool_size = Gauge(
    "interceptor_db_pool_size", "USE: current asyncpg pool size (the superuser pool)."
)
db_pool_in_use = Gauge(
    "interceptor_db_pool_in_use",
    "USE: connections currently checked out of the pool (size - idle).",
)

bastion_outbox_unpublished_total = Gauge(
    "bastion_outbox_unpublished_total",
    "USE: rows in outbox_events not yet published to Kafka — a growing "
    "value means the outbox publisher is falling behind.",
)

bastion_call_cost_dollars_total = Counter(
    "bastion_call_cost_dollars_total",
    "Business: cumulative cost across every CallCompleted event that reported one.",
)
