"""Kafka topic names — one place both the outbox publisher (interceptor)
and every consumer (aggregator, analytics, security) agree on them.
UPGRADE_ARCHITECTURE.md §4.3: partitioned by trace_id, so events within one
causal execution are ordered; no ordering guarantee across traces.
"""

from __future__ import annotations

TOOL_EVENTS_TOPIC = "tool-events"
