"""Prometheus metrics — U12 (v2 upgrade), UPGRADE_ARCHITECTURE.md §14.
The aggregator had no app-specific metrics at all before this phase
(`/metrics` existed but only ever emitted `prometheus_client`'s default
process/GC metrics) — see interceptor/metrics.py's module docstring for
the RED/USE/business framing this mirrors.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Prefixed with `aggregator_`, not the generic name — see
# interceptor/metrics.py's comment on the same pattern: prometheus_client's
# CollectorRegistry is process-wide global, and this repo's own test suite
# imports both packages into one process for cross-service tests, which
# hit a real DuplicateTimeseries error the first time these were left
# unprefixed and identically named on both sides.
http_requests_total = Counter(
    "aggregator_http_requests_total",
    "RED: every request handled, by method/path/status.",
    labelnames=["method", "path", "status_code"],
)

http_request_duration_seconds = Histogram(
    "aggregator_http_request_duration_seconds",
    "RED: request duration by method/path.",
    labelnames=["method", "path"],
)

db_pool_size = Gauge("aggregator_db_pool_size", "USE: current asyncpg pool size.")
db_pool_in_use = Gauge(
    "aggregator_db_pool_in_use",
    "USE: connections currently checked out of the pool (size - idle).",
)

bastion_active_traces = Gauge(
    "bastion_active_traces",
    "Business/USE hybrid: traces currently tracked as running in-memory "
    "(main.py's active_traces) — a proxy for how much live work this "
    "instance is holding state for.",
)

bastion_live_ws_connections = Gauge(
    "bastion_live_ws_connections",
    "Business: WebSocket clients currently connected across all agents on this instance.",
)
