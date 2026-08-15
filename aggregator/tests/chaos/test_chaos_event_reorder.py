"""U14 chaos scenario (UPGRADE_ARCHITECTURE.md §16): "Reorder events within
a partition (simulate)" — the one scenario in the table with no "must
hold" invariant; the required deliverable is instead to "document what
breaks, if anything, and why partition-key ordering is assumed to prevent
this in practice."

This can only be tested by simulation, exactly as the architecture doc's
own wording allows: `fold_events_to_graph` is called directly with a
hand-built out-of-order event list, bypassing Kafka/the outbox entirely —
there is no way to make the real production pipeline actually reorder
same-trace events, because production's own partitioning key IS trace_id
(UPGRADE_ARCHITECTURE.md's Kafka partitioning ADR-014), which guarantees
FIFO delivery within a partition. That guarantee is exactly what this test
demonstrates the *consequence of losing* would be.

Real finding: reordering doesn't raise an exception or produce an
obviously-wrong result an assertion would catch downstream — it fails
silently. `fold_events_to_graph` (aggregator/src/bastion_aggregator/graph.py)
looks up `nodes.get(span_id)` for any non-CallAttempted event and
*silently skips* it if the node doesn't exist yet ("defensive: shouldn't
happen, every span starts with CallAttempted" — true only under in-order
delivery). If CallAllowed/CallCompleted arrive before CallAttempted, both
are dropped on the floor: the node gets created afterward by the delayed
CallAttempted, frozen at status "pending" forever, and if the reordered
event was the *root* span's terminal event, `ended_at` never gets set
either — the whole trace is stuck reporting status "running" permanently,
with no error anywhere to signal it. This is a genuine latent fragility
in the fold function, not a hypothetical: it's the reason same-partition
ordering is load-bearing, not just a performance nicety. Not fixed in this
phase — hardening the fold to tolerate out-of-order delivery would mean
either buffering/re-sorting by sequence_number (real design work, more
than a "while we're here" patch) or an assertion that fails loudly instead
of silently, and either belongs in a phase that decides to spend budget on
it deliberately, not a chaos test's side effect. Documented here and in
docs/CHAOS_RESULTS.md as an accepted, understood risk contingent on
Kafka's partitioning guarantee holding.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from bastion_aggregator.graph import fold_events_to_graph

TRACE_ID = uuid.uuid4()
AGENT_ID = uuid.uuid4()
SPAN_ID = uuid.uuid4()


def _event(
    *,
    span_id: UUID,
    parent_span_id: UUID | None,
    event_type: str,
    payload: dict[str, Any],
    sequence_number: int,
) -> dict[str, Any]:
    return {
        "trace_id": TRACE_ID,
        "agent_id": AGENT_ID,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "event_type": event_type,
        "payload": payload,
        "created_at": datetime.now(UTC),
        "sequence_number": sequence_number,
    }


def _in_order_events() -> list[dict[str, Any]]:
    return [
        _event(
            span_id=SPAN_ID,
            parent_span_id=None,
            event_type="CallAttempted",
            payload={"tool_name": "chaos.reorder_test", "args": {}},
            sequence_number=1,
        ),
        _event(
            span_id=SPAN_ID,
            parent_span_id=None,
            event_type="CallAllowed",
            payload={"policy_id": None, "decision": "allowed"},
            sequence_number=2,
        ),
        _event(
            span_id=SPAN_ID,
            parent_span_id=None,
            event_type="CallCompleted",
            payload={"latency_ms": 2.0, "cost": None, "result": {"ok": True}},
            sequence_number=3,
        ),
    ]


def test_in_order_events_fold_correctly_as_a_baseline() -> None:
    graph = fold_events_to_graph(_in_order_events())
    assert graph.status == "completed"
    assert graph.nodes[0].status == "completed"
    assert graph.ended_at is not None


def test_reordered_events_silently_drop_updates_and_the_trace_never_terminates() -> None:
    """Same three events, delivered as CallCompleted, CallAllowed,
    CallAttempted — a same-trace reorder that same-partition Kafka
    ordering is specifically relied on to prevent. See module docstring
    for the full explanation of what this demonstrates."""
    reordered = list(reversed(_in_order_events()))

    graph = fold_events_to_graph(reordered)  # does not raise

    assert graph.status == "running", (
        "the trace never reaches a terminal status because the root span's "
        "terminal event (CallCompleted) was silently dropped before its "
        "node existed"
    )
    assert graph.ended_at is None
    assert len(graph.nodes) == 1
    assert graph.nodes[0].status == "pending", (
        "both CallAllowed and CallCompleted were silently skipped — the "
        "node is permanently stuck at its initial status"
    )
    assert graph.nodes[0].latency_ms is None
    assert graph.nodes[0].cost is None
