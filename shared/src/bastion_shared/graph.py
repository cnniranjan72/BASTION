"""GET /traces/{id}, GET /traces/{id}/events — the folded causal graph a
past (or still-running) trace replays into. This is also what Phase 6's
live WebSocket deltas and Phase 7's 3D view will consume — one shape, three
consumers, matching bastion_shared's whole reason to exist (§7 of
ARCHITECTURE.md).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NodeStatus = Literal["pending", "allowed", "blocked", "pending_approval", "completed", "failed"]
TraceStatus = Literal["running", "completed", "failed", "had_blocks"]


class GraphNode(BaseModel):
    span_id: UUID
    parent_span_id: UUID | None
    tool_name: str
    status: NodeStatus
    args: dict[str, Any] | None = None
    latency_ms: float | None = None
    cost: float | None = None
    reason: str | None = None  # block/deny/failure reason, when applicable


class GraphEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: UUID = Field(alias="from")
    to: UUID


class TraceGraph(BaseModel):
    trace_id: UUID
    agent_id: UUID
    status: TraceStatus
    total_cost: float
    total_calls: int
    blocked_calls: int
    started_at: datetime
    ended_at: datetime | None
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class TraceSummaryResponse(BaseModel):
    """GET /traces — list view, no nodes/edges (that's GET /traces/{id})."""

    trace_id: UUID
    agent_id: UUID
    status: TraceStatus
    total_cost: float
    total_calls: int
    blocked_calls: int
    started_at: datetime
    ended_at: datetime | None
