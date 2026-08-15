"""U16 (v2 upgrade): the supporting-surfaces read endpoints FRONTEND_V2.md
asks for (Command Center, Threat Center, Agent Health, Cost Center) --
served by the aggregator, same rationale as `GET /traces*` in policy_api.py's
neighbor `graph.py`: it already owns the read-model these are computed
from. Every number here traces back to a real aggregate over `events`/
`trace_summaries`/`policies`/`agents` -- see docs/adr/ADR-021 for the
handful of places the spec's own mock text ("99.97% availability") isn't
literally something this system tracks, and what real, honestly-labeled
substitute was used instead.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ToolCount(BaseModel):
    tool_name: str
    count: int


class PolicyViolationCount(BaseModel):
    policy_id: UUID
    policy_name: str
    block_count: int


class ThreatTimelineBucket(BaseModel):
    day: datetime
    blocked_count: int


class ThreatSummaryResponse(BaseModel):
    """ADR-021: "threats" = blocked calls, the one thing the policy engine
    actually enforces against -- no separate prompt-injection-specific
    detector exists in this codebase, so this never claims to surface one."""

    window_days: int
    blocked_calls_total: int
    top_violated_policies: list[PolicyViolationCount]
    timeline: list[ThreatTimelineBucket]


class AnomalyFlag(BaseModel):
    description: str


class AgentHealthResponse(BaseModel):
    agent_id: UUID
    agent_name: str
    window_days: int
    calls_total: int
    blocked_total: int
    failed_total: int
    pending_approval_total: int
    avg_latency_ms: float | None
    estimated_cost_total: float
    top_tools: list[ToolCount]
    # ADR-021: composite 0-100 score and its four real inputs -- the exact
    # formula (and why these weights) is documented there, not just here.
    health_score: float
    reliability: float
    policy_compliance: float
    tool_error_rate: float
    approval_rate: float
    anomalies: list[AnomalyFlag]


class CostByAgent(BaseModel):
    agent_id: UUID
    agent_name: str
    cost: float


class CostByTool(BaseModel):
    tool_name: str
    cost: float


class CostSummaryResponse(BaseModel):
    window_days: int
    total_cost: float
    by_agent: list[CostByAgent]
    by_tool: list[CostByTool]
    # ADR-021: sum over (blocked_call_count * that agent+tool's own real
    # historical avg cost per completed call) -- an estimate by construction
    # (a blocked call never runs, so it never has a real recorded cost), but
    # built from this org's real completed-call cost data, not a guess.
    estimated_savings_from_policy_enforcement: float


class LiveActivityEntry(BaseModel):
    agent_id: UUID
    agent_name: str
    tool_name: str
    decision: str
    at: datetime


class CommandCenterSnapshotResponse(BaseModel):
    agents_total: int
    # ADR-021: an agent counts as unhealthy here if it currently has at
    # least one OPEN circuit breaker (interceptor/circuit_breaker.py's real
    # Redis state) -- not a synthetic/demo status.
    agents_healthy: int
    # ADR-021: real call-success rate (CallCompleted / (CallCompleted +
    # CallFailed)) over window_days, not literal infra uptime -- no
    # uptime-history mechanism exists anywhere in this system to report
    # that honestly.
    availability_pct: float
    window_days: int
    last_incident_at: datetime | None
    recent_activity: list[LiveActivityEntry]
