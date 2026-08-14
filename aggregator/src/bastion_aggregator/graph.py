"""Folds an ordered event list into a TraceGraph — the actual event-sourcing
discipline (CLAUDE.md rule #1): current state is never stored directly, it's
always derived by replaying events in order. Used both by the aggregator's
live in-memory tracking (graph.py callers in listener.py) and by
GET /traces/{id} for on-demand replay when no trace_summaries row exists yet
(an active trace, or one whose projection was never built) — same fold,
same code path, so there's exactly one place this logic can be wrong.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from bastion_shared import GraphEdge, GraphNode, NodeStatus, TraceGraph, TraceStatus

# ApprovalGranted/ApprovalDenied are the allowed/blocked-equivalent for a
# span resolved via the approval flow (Phase 3) — it never gets its own
# separate CallAllowed/CallBlocked event. See docs/ARCHITECTURE.md §13.
STATUS_FOR_EVENT_TYPE: dict[str, NodeStatus] = {
    "CallAllowed": "allowed",
    "ApprovalGranted": "allowed",
    "CallBlocked": "blocked",
    "ApprovalDenied": "blocked",
    "CallPendingApproval": "pending_approval",
    "CallCompleted": "completed",
    "CallFailed": "failed",
}


def fold_events_to_graph(events: list[Any]) -> TraceGraph:
    """events: asyncpg.Record rows (or anything dict-like) for a single
    trace_id, ordered by sequence_number ascending. Raises ValueError if
    empty — a trace only exists because at least one event was emitted."""
    if not events:
        raise ValueError("cannot fold an empty event list into a graph")

    nodes: dict[UUID, GraphNode] = {}
    edges: list[GraphEdge] = []
    trace_id = events[0]["trace_id"]
    agent_id = events[0]["agent_id"]
    started_at = events[0]["created_at"]
    root_span_id: UUID | None = None
    ended_at = None

    for event in events:
        span_id = event["span_id"]
        event_type = event["event_type"]
        payload = event["payload"]

        if event_type == "CallAttempted":
            parent_span_id = event["parent_span_id"]
            nodes[span_id] = GraphNode(
                span_id=span_id,
                parent_span_id=parent_span_id,
                tool_name=payload.get("tool_name", ""),
                status="pending",
                args=payload.get("args"),
            )
            if parent_span_id is not None:
                edges.append(GraphEdge(**{"from": parent_span_id, "to": span_id}))
            else:
                root_span_id = span_id
            continue

        node = nodes.get(span_id)
        if node is None:
            continue  # defensive: shouldn't happen, every span starts with CallAttempted

        new_status = STATUS_FOR_EVENT_TYPE.get(event_type)
        if new_status is not None:
            node.status = new_status
        if event_type in ("CallBlocked", "ApprovalDenied"):
            node.reason = payload.get("reason")
        if event_type in ("CallCompleted", "CallFailed"):
            node.latency_ms = payload.get("latency_ms")
            node.cost = payload.get("cost")
            if event_type == "CallFailed":
                node.reason = payload.get("error")

        # The trace ends when its *root* span reaches a terminal state — by
        # construction, the root's execute() only returns after every nested
        # call it awaited has already completed (see docs/ARCHITECTURE.md
        # §8), so this is a reliable signal the whole trace is done.
        if span_id == root_span_id and event_type in (
            "CallCompleted",
            "CallFailed",
            "CallBlocked",
            "ApprovalDenied",
        ):
            ended_at = event["created_at"]

    total_cost = sum(n.cost or 0 for n in nodes.values())
    blocked_calls = sum(1 for n in nodes.values() if n.status == "blocked")
    failed = any(n.status == "failed" for n in nodes.values())

    status: TraceStatus
    if ended_at is None:
        status = "running"
    elif failed:
        status = "failed"
    elif blocked_calls > 0:
        status = "had_blocks"
    else:
        status = "completed"

    return TraceGraph(
        trace_id=trace_id,
        agent_id=agent_id,
        status=status,
        total_cost=float(total_cost),
        total_calls=len(nodes),
        blocked_calls=blocked_calls,
        started_at=started_at,
        ended_at=ended_at,
        nodes=list(nodes.values()),
        edges=edges,
    )
