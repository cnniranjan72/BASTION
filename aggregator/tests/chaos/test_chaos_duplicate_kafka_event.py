"""U14 chaos scenario (UPGRADE_ARCHITECTURE.md §16): "Duplicate a Kafka
event" — required invariant: "downstream fold is idempotent — duplicate
has zero effect on derived state."

What's actually duplicable in this system, precisely: events themselves
are written to Postgres exactly once (the interceptor's insert_event, one
INSERT per event) — what Kafka's at-least-once delivery can duplicate is
the *notification* telling the aggregator "trace X changed, go re-fold
it." `_handle_notification` (main.py) already re-fetches and re-folds the
*entire* trace fresh from Postgres on every single message rather than
incrementally applying the message's own payload — this test proves that
design choice is what it's claimed to be: calling it twice with the
identical notification payload (the exact scenario a real duplicate
Kafka delivery produces) must be indistinguishable from calling it once.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from bastion_aggregator.db import db as aggregator_db
from bastion_aggregator.main import _handle_notification, active_traces
from bastion_interceptor.db import db as interceptor_db
from bastion_shared import EventType


async def test_duplicate_notification_for_the_same_event_has_zero_effect_on_derived_state(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, _ = test_agent
    trace_id = uuid.uuid4()
    span_id = uuid.uuid4()

    async def _write(event_type: EventType, payload: dict) -> None:
        await interceptor_db.insert_event(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=None,
            agent_id=agent_id,
            event_type=event_type,
            payload=payload,
        )

    await _write(EventType.CALL_ATTEMPTED, {"tool_name": "chaos.duplicate_test", "args": {}})
    await _write(EventType.CALL_ALLOWED, {"policy_id": None, "decision": "allowed"})
    await _write(
        EventType.CALL_COMPLETED, {"latency_ms": 3.0, "cost": None, "result": {"ok": True}}
    )

    notification = {
        "trace_id": str(trace_id),
        "span_id": str(span_id),
        "agent_id": str(agent_id),
        "event_type": "CallCompleted",
        "parent_span_id": None,
        "payload": {"latency_ms": 3.0, "cost": None, "result": {"ok": True}},
    }

    # The real, once-only delivery.
    await _handle_notification(notification)
    summary_after_once = await aggregator_db.get_trace_summary(trace_id)
    assert summary_after_once is not None
    assert summary_after_once["total_calls"] == 1
    assert summary_after_once["status"] == "completed"

    # A duplicate delivery of the *identical* message — Kafka's
    # at-least-once guarantee means this genuinely happens in production.
    await _handle_notification(notification)
    summary_after_duplicate = await aggregator_db.get_trace_summary(trace_id)
    assert summary_after_duplicate is not None
    assert summary_after_duplicate["total_calls"] == 1, (
        "duplicate notification must not double-count calls"
    )
    assert summary_after_duplicate["status"] == "completed"
    assert dict(summary_after_duplicate) == dict(summary_after_once), (
        "duplicate notification produced a different persisted summary than the original"
    )

    # No trailing "running" ghost entry left behind in the in-memory
    # tracking either — a terminal trace is always evicted, whether this
    # is the first or a duplicate notification observing that terminal state.
    assert trace_id not in active_traces
