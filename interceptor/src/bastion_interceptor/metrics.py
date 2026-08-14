"""Prometheus metrics (BUILD_PLAN.md Phase 9): `intercept_latency_seconds`
and `policy_decisions_total{decision=}`, exposed at GET /metrics in the
standard text-exposition format (`prometheus_client.generate_latest`).

Deliberately just these two, not a broader metrics sweep — they're the two
BUILD_PLAN.md names explicitly, and they're the two that answer the
questions this service actually needs to answer on a dashboard: is the
hot path fast, and what fraction of calls are being blocked/approved vs.
allowed.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

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
