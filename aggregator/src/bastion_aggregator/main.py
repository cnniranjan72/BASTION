"""Aggregator service — event-stream subscriber, graph builder, WS fan-out
(ARCHITECTURE.md §2.5).

Phase 4: subscribes to `events` via Postgres LISTEN/NOTIFY (listener.py),
folds each notified trace's events into a TraceGraph (graph.py) — the same
fold GET /traces/{id} uses on demand — and persists trace_summaries once a
trace reaches a terminal state (docs/ARCHITECTURE.md §14).

U3 (v2 upgrade): the trigger mechanism moved from Postgres LISTEN/NOTIFY to
consuming Kafka's tool-events topic (kafka_consumer.py) — Postgres stays
the durable source of truth (the transactional outbox writes here first;
see interceptor/db.py), Kafka is fan-out/distribution only
(UPGRADE_ARCHITECTURE.md §4.2). `_handle_notification` below is completely
unchanged: it re-fetches and re-folds the whole trace from Postgres on
every message, which is exactly what makes Kafka's at-least-once delivery
safe to consume without a separate dedup step.

Phase 5: every /traces endpoint requires a real access token (human_auth.py,
verify-only — the aggregator never issues tokens) and is scoped to the
caller's own org, derived from the JWT rather than an explicit `org_id`
param.

Phase 6: WS /live/{agent_id} (ws.py's ConnectionManager) pushes graph deltas
straight from the same LISTEN/NOTIFY handler that already updates
active_traces — no polling on either side. A browser can't set a custom
Authorization header on a WebSocket handshake, so the access token travels
as a query param instead and is verified via human_auth.decode_bearer_token
directly (not the Header-based FastAPI dependency the HTTP endpoints use).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from bastion_shared import (
    AgentHealthResponse,
    AnomalyFlag,
    CommandCenterSnapshotResponse,
    CostByAgent,
    CostByTool,
    CostSummaryResponse,
    EdgeAddedMessage,
    InvalidAccessToken,
    LiveActivityEntry,
    LiveNode,
    NodeAddedMessage,
    NodeUpdatedMessage,
    PolicyViolationCount,
    ThreatSummaryResponse,
    ThreatTimelineBucket,
    ToolCount,
    TraceGraph,
    TraceSummaryResponse,
)
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import tracing
from .config import config
from .db import db
from .graph import STATUS_FOR_EVENT_TYPE, fold_events_to_graph
from .human_auth import AuthenticatedUser, authenticate_user, decode_bearer_token
from .kafka_consumer import KafkaEventConsumer
from .logging import configure_logging, log
from .metrics import (
    bastion_active_traces,
    bastion_live_ws_connections,
    db_pool_in_use,
    db_pool_size,
    http_request_duration_seconds,
    http_requests_total,
)
from .redis_bus import redis_bus
from .ws import manager as ws_manager

configure_logging()

require_any_role = Depends(authenticate_user)

# U3 (v2 upgrade): consumes tool-events instead of v1's Postgres
# LISTEN/NOTIFY (listener.py, kept in the tree, no longer wired here — see
# docs/adr/ADR-002). group_id="aggregator" is this consumer group's
# identity — its committed offset is what "resumes correctly after a
# restart" actually means (proven in the U3 milestone test).
kafka_consumer = KafkaEventConsumer(group_id="aggregator")
# Live in-memory tracking (ARCHITECTURE.md §2.5) — updated on every
# notification, evicted once a trace is persisted to trace_summaries and no
# longer "active." Not the source of truth (events/trace_summaries are);
# also what WS /live/{agent_id} deltas are pushed from (below).
active_traces: dict[UUID, TraceGraph] = {}

# Events whose node is brand new vs. an update to an existing one — decides
# node_added (+ edge_added if it has a parent) vs. node_updated.
_CREATION_EVENT = "CallAttempted"


async def _handle_notification(data: dict[str, Any]) -> None:
    trace_id = UUID(data["trace_id"])
    span_id = UUID(data["span_id"])
    agent_id = UUID(data["agent_id"])
    event_type = data["event_type"]
    payload = data.get("payload") or {}

    # U3 (v2 upgrade): broadcast exactly what *this* event represents,
    # derived from its own type + payload — not a fresh fold of the
    # trace's current overall state. A real bug found by U3's milestone
    # test: v1's original design re-folded the whole trace on every
    # notification and broadcast whatever the *current* status happened to
    # be, which worked under Postgres LISTEN/NOTIFY's near-zero latency
    # (the next event essentially never existed yet by the time a
    # notification was handled) but silently drops intermediate statuses
    # under Kafka's real, if still small, delivery latency — if
    # CallAllowed and CallCompleted both land in Postgres before the
    # CallAllowed notification is processed, the old code broadcast
    # "completed" twice and "allowed" never.
    if event_type == _CREATION_EVENT:
        await ws_manager.broadcast(
            agent_id,
            NodeAddedMessage(
                trace_id=trace_id,
                node=LiveNode(
                    span_id=span_id, tool_name=payload.get("tool_name", ""), status="pending"
                ),
            ),
        )
        parent_span_id_raw = data.get("parent_span_id")
        if parent_span_id_raw:
            await ws_manager.broadcast(
                agent_id,
                EdgeAddedMessage.model_validate({"from": UUID(parent_span_id_raw), "to": span_id}),
            )
    else:
        status = STATUS_FOR_EVENT_TYPE.get(event_type)
        # STATUS_FOR_EVENT_TYPE's values never actually include "pending"
        # (only _CREATION_EVENT produces that, handled above) — the
        # exclusion here is for mypy's benefit narrowing NodeStatus down to
        # NodeUpdatedMessage's own status literal, which doesn't accept it.
        if status is not None and status != "pending":
            await ws_manager.broadcast(
                agent_id,
                NodeUpdatedMessage(
                    trace_id=trace_id,
                    span_id=span_id,
                    status=status,
                    latency_ms=payload.get("latency_ms"),
                    cost=payload.get("cost"),
                    reason=payload.get("reason") or payload.get("error"),
                ),
            )

    # Trace-terminal detection and trace_summaries persistence legitimately
    # want the *current* overall state, unlike the per-notification
    # broadcast above — a fresh fold is correct here.
    events = await db.get_events_for_trace(trace_id)
    if not events:
        return
    graph = fold_events_to_graph(events)

    if graph.status == "running":
        active_traces[trace_id] = graph
        return

    org_id = await db.get_org_id_for_agent(graph.agent_id)
    if org_id is not None:
        await db.upsert_trace_summary(org_id=org_id, graph=graph)
        log.info("trace summary persisted", trace_id=str(trace_id), status=graph.status)
    active_traces.pop(trace_id, None)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.connect()
    # U11 (v2 upgrade): ws.py's ConnectionManager publishes/subscribes
    # through this — must be connected before kafka_consumer.start() below,
    # since a notification can arrive (and call ws_manager.broadcast(),
    # which publishes) as soon as the consumer starts.
    await redis_bus.connect()
    await kafka_consumer.start(_handle_notification)
    try:
        yield
    finally:
        await kafka_consumer.stop()
        await redis_bus.close()
        await db.close()


app = FastAPI(title="bastion-aggregator", version="0.0.0", lifespan=lifespan)
tracing.configure_tracing(app, service_name="bastion-aggregator")


def _error_body(request: Request, code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": request.state.request_id}}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    body = exc.detail if isinstance(exc.detail, dict) and "error" in exc.detail else None
    if body is None:
        body = _error_body(request, "HTTP_ERROR", str(exc.detail))
    return JSONResponse(status_code=exc.status_code, content=body)


def _format_validation_errors(exc: RequestValidationError) -> str:
    # Same reasoning as interceptor/src/bastion_interceptor/main.py's
    # identical helper: str(exc.errors()) used to dump a raw Python repr
    # straight into the user-facing error message.
    parts = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"] if part != "body")
        parts.append(f"{loc}: {error['msg']}" if loc else error["msg"])
    return "; ".join(parts) if parts else "invalid request"


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_error_body(request, "VALIDATION_ERROR", _format_validation_errors(exc)),
    )


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    structlog.contextvars.bind_contextvars(request_id=request_id)
    start = time.perf_counter()
    log.info("request received", method=request.method, path=request.url.path)
    response: Response | None = None
    try:
        response = await call_next(request)
        return response
    finally:
        elapsed_seconds = time.perf_counter() - start
        status_code = response.status_code if response is not None else 500
        log.info(
            "request completed",
            status_code=status_code,
            elapsed_ms=round(elapsed_seconds * 1000, 2),
        )
        if response is not None:
            response.headers["X-Request-Id"] = request_id
        # U12 (v2 upgrade), RED metrics — same route-template-not-raw-path
        # reasoning as interceptor/main.py's identical middleware.
        route = request.scope.get("route")
        path_label = route.path if route is not None else request.url.path
        http_requests_total.labels(
            method=request.method, path=path_label, status_code=str(status_code)
        ).inc()
        http_request_duration_seconds.labels(method=request.method, path=path_label).observe(
            elapsed_seconds
        )
        structlog.contextvars.clear_contextvars()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "aggregator"}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    db_pool_size.set(db.pool.get_size())
    db_pool_in_use.set(db.pool.get_size() - db.pool.get_idle_size())
    bastion_active_traces.set(len(active_traces))
    bastion_live_ws_connections.set(ws_manager.connection_count())
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.websocket("/live/{agent_id}")
async def live(websocket: WebSocket, agent_id: UUID) -> None:
    """API_SPEC.md's Realtime API. Auth via `?token=<access token>` query
    param, not a header — browsers don't let JS set one on the WebSocket
    handshake. Closed with 4401/4403 (the 4xxx range is reserved for
    application-defined WS close codes) for missing/invalid auth or a
    cross-org agent_id, mirroring the HTTP endpoints' 401/403."""
    token = websocket.query_params.get("token")
    if token is None:
        await websocket.close(code=4401, reason="missing token")
        return
    try:
        user = decode_bearer_token(token)
    except InvalidAccessToken:
        await websocket.close(code=4401, reason="invalid token")
        return

    org_id = await db.get_org_id_for_agent(agent_id)
    if org_id is None or org_id != user.org_id:
        await websocket.close(code=4403, reason="forbidden")
        return

    await ws_manager.connect(agent_id, websocket)
    # U14 chaos finding ("drop WS connection mid-session, client reconnects
    # and correctly resyncs current graph state, not just future deltas"):
    # a reconnecting client used to see nothing but whatever happened
    # *after* this call — everything that happened while it was
    # disconnected was silently lost, since ConnectionManager only ever
    # broadcasts forward. connect() is called first so no future delta can
    # be missed; the resync snapshot below replays active_traces as of
    # right now through the exact same message types a live client already
    # knows how to reduce (node_added [+ edge_added], then node_updated to
    # the real current status) — no new client-side handling needed. The
    # one accepted tradeoff of connect-then-snapshot ordering: a live event
    # landing in the narrow window between connect() and the snapshot read
    # could be replayed twice (once live, once in the snapshot) — a
    # harmless duplicate. The alternative ordering (snapshot-then-connect)
    # would instead risk silently *missing* that event, which is strictly
    # worse for this invariant.
    await _send_resync_snapshot(websocket, agent_id)
    try:
        while True:
            # No client->server protocol defined yet; just keep the
            # connection alive and detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(agent_id, websocket)


async def _send_resync_snapshot(websocket: WebSocket, agent_id: UUID) -> None:
    for graph in list(active_traces.values()):
        if graph.agent_id != agent_id:
            continue
        for node in graph.nodes:
            await websocket.send_json(
                NodeAddedMessage(
                    trace_id=graph.trace_id,
                    node=LiveNode(span_id=node.span_id, tool_name=node.tool_name, status="pending"),
                ).model_dump(mode="json", by_alias=True)
            )
            if node.parent_span_id is not None:
                await websocket.send_json(
                    EdgeAddedMessage.model_validate(
                        {"from": node.parent_span_id, "to": node.span_id}
                    ).model_dump(mode="json", by_alias=True)
                )
            if node.status != "pending":
                await websocket.send_json(
                    NodeUpdatedMessage(
                        trace_id=graph.trace_id,
                        span_id=node.span_id,
                        status=node.status,
                        latency_ms=node.latency_ms,
                        cost=node.cost,
                        reason=node.reason,
                    ).model_dump(mode="json", by_alias=True)
                )


def _not_found(request: Request, trace_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "error": {
                "code": "TRACE_NOT_FOUND",
                "message": f"no trace {trace_id}",
                "request_id": request.state.request_id,
            }
        },
    )


@app.get("/traces/{trace_id}")
async def get_trace(
    trace_id: UUID, request: Request, user: AuthenticatedUser = require_any_role
) -> TraceGraph:
    """Full replay: trace_summaries.graph_snapshot if a persisted projection
    exists (fast path), else folds `events` fresh — covers active/in-progress
    traces and demonstrates the event-sourcing discipline (current state is
    always derivable from `events`, the cache is a pure accelerator, not the
    source of truth — CLAUDE.md rule #1). A trace from another org 404s —
    same rationale as interceptor.py's activate_policy: don't distinguish
    "doesn't exist" from "exists but isn't yours."
    """
    summary = await db.get_trace_summary(trace_id)
    if summary is not None:
        if summary["org_id"] != user.org_id:
            raise _not_found(request, trace_id)
        return TraceGraph.model_validate(summary["graph_snapshot"])

    graph = active_traces.get(trace_id)
    if graph is None:
        events = await db.get_events_for_trace(trace_id)
        if not events:
            raise _not_found(request, trace_id)
        graph = fold_events_to_graph(events)

    org_id = await db.get_org_id_for_agent(graph.agent_id)
    if org_id != user.org_id:
        raise _not_found(request, trace_id)
    return graph


@app.get("/traces/{trace_id}/events")
async def get_trace_events(
    trace_id: UUID, request: Request, user: AuthenticatedUser = require_any_role
) -> list[dict[str, Any]]:
    """Raw event list, for the 2D inspector panel (ARCHITECTURE.md §2.6)."""
    events = await db.get_events_for_trace(trace_id)
    if not events:
        raise _not_found(request, trace_id)
    org_id = await db.get_org_id_for_agent(events[0]["agent_id"])
    if org_id != user.org_id:
        raise _not_found(request, trace_id)
    return [dict(e) for e in events]


@app.get("/traces")
async def list_traces(
    user: AuthenticatedUser = require_any_role,
    agent_id: UUID | None = None,
    status: str | None = None,
    tool: str | None = None,
    policy: str | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
) -> list[TraceSummaryResponse]:
    """Persisted (terminal-state) traces only — an in-progress trace has no
    trace_summaries row yet by design (docs/ARCHITECTURE.md §14).

    U16 (v2 upgrade), Trace Explorer: agent_id/status/tool/policy/
    started_after/started_before were "not implemented yet" per
    API_SPEC.md until now — see db.list_trace_summaries for how each one
    actually queries."""
    records = await db.list_trace_summaries(
        user.org_id,
        agent_id=agent_id,
        status=status,
        tool_name=tool,
        policy_name=policy,
        started_after=started_after,
        started_before=started_before,
    )
    return [
        TraceSummaryResponse(
            trace_id=r["trace_id"],
            agent_id=r["agent_id"],
            status=r["status"],
            total_cost=float(r["total_cost"]),
            total_calls=r["total_calls"],
            blocked_calls=r["blocked_calls"],
            started_at=r["started_at"],
            ended_at=r["ended_at"],
        )
        for r in records
    ]


@app.get("/threats")
async def get_threats(
    user: AuthenticatedUser = require_any_role,
    window_days: int = 30,
) -> ThreatSummaryResponse:
    """U16 (v2 upgrade), Threat Center. See docs/adr/ADR-021: "threats"
    means blocked calls -- no separate prompt-injection detector exists."""
    summary = await db.get_threat_summary(user.org_id, window_days=window_days)
    return ThreatSummaryResponse(
        window_days=window_days,
        blocked_calls_total=summary["blocked_total"],
        top_violated_policies=[
            PolicyViolationCount(
                policy_id=r["policy_id"], policy_name=r["policy_name"], block_count=r["block_count"]
            )
            for r in summary["top_policies"]
        ],
        timeline=[
            ThreatTimelineBucket(day=r["day"], blocked_count=r["blocked_count"])
            for r in summary["timeline"]
        ],
    )


@app.get("/agents/{agent_id}/health")
async def get_agent_health(
    agent_id: UUID,
    request: Request,
    user: AuthenticatedUser = require_any_role,
    window_days: int = 30,
) -> AgentHealthResponse:
    """U16 (v2 upgrade), Agent Health. Score formula and anomaly-detection
    baseline are both real computed aggregates -- see docs/adr/ADR-021 for
    the exact definitions and why those weights."""
    org_id = await db.get_org_id_for_agent(agent_id)
    if org_id is None or org_id != user.org_id:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "AGENT_NOT_FOUND",
                    "message": f"no agent {agent_id}",
                    "request_id": request.state.request_id,
                }
            },
        )

    agent_name = await db.get_agent_name(agent_id)
    assert agent_name is not None
    stats = await db.get_agent_stats(agent_id, window_days=window_days)
    top_tools = await db.get_agent_top_tools(agent_id, window_days=window_days)
    trend = await db.get_agent_call_rate_trend(agent_id)

    calls_total = stats["calls_total"] or 0
    blocked_total = stats["blocked_total"] or 0
    failed_total = stats["failed_total"] or 0
    pending_approval_total = stats["pending_approval_total"] or 0
    completed_total = calls_total - blocked_total - failed_total - pending_approval_total
    reliability = (
        completed_total / (completed_total + failed_total)
        if (completed_total + failed_total)
        else 1.0
    )
    policy_compliance = 1 - (blocked_total / calls_total) if calls_total else 1.0
    tool_error_rate = failed_total / calls_total if calls_total else 0.0
    approval_rate = pending_approval_total / calls_total if calls_total else 0.0

    org_avg_cost = await db.pool.fetchval(
        """
        SELECT AVG((e.payload->>'cost')::numeric)
        FROM events e JOIN agents a ON a.id = e.agent_id
        WHERE a.org_id = $1 AND e.event_type = 'CallCompleted'
          AND e.created_at >= now() - make_interval(days => $2)
        """,
        user.org_id,
        window_days,
    )
    agent_avg_cost = (
        float(stats["estimated_cost_total"]) / completed_total if completed_total else None
    )
    cost_efficiency = (
        min(float(org_avg_cost) / agent_avg_cost, 1.5) / 1.5
        if org_avg_cost and agent_avg_cost
        else 1.0
    )

    health_score = 100 * (
        (reliability + policy_compliance + (1 - tool_error_rate) + cost_efficiency) / 4
    )

    anomalies: list[AnomalyFlag] = []
    last_24h = trend["last_24h"] or 0
    prior_7d = trend["prior_7d"] or 0
    baseline_daily_avg = prior_7d / 7
    if baseline_daily_avg > 0 and last_24h / baseline_daily_avg >= 2.0:
        ratio = last_24h / baseline_daily_avg
        anomalies.append(
            AnomalyFlag(
                description=f"tool-call frequency increased {ratio:.1f}× over baseline "
                f"({last_24h} calls in the last 24h vs. a {baseline_daily_avg:.1f}/day average "
                "over the preceding 7 days)"
            )
        )

    return AgentHealthResponse(
        agent_id=agent_id,
        agent_name=agent_name,
        window_days=window_days,
        calls_total=calls_total,
        blocked_total=blocked_total,
        failed_total=failed_total,
        pending_approval_total=pending_approval_total,
        avg_latency_ms=float(stats["avg_latency_ms"])
        if stats["avg_latency_ms"] is not None
        else None,
        estimated_cost_total=float(stats["estimated_cost_total"]),
        top_tools=[ToolCount(tool_name=r["tool_name"], count=r["count"]) for r in top_tools],
        health_score=round(health_score, 1),
        reliability=round(reliability, 3),
        policy_compliance=round(policy_compliance, 3),
        tool_error_rate=round(tool_error_rate, 3),
        approval_rate=round(approval_rate, 3),
        anomalies=anomalies,
    )


@app.get("/costs")
async def get_costs(
    user: AuthenticatedUser = require_any_role,
    window_days: int = 30,
) -> CostSummaryResponse:
    """U16 (v2 upgrade), Cost Center. "Estimated savings" is a real estimate
    built from this org's own historical cost data -- see docs/adr/ADR-021,
    never a fabricated number."""
    by_agent = await db.get_cost_by_agent(user.org_id, window_days=window_days)
    by_tool = await db.get_cost_by_tool(user.org_id, window_days=window_days)
    savings = await db.get_estimated_savings_from_policy_enforcement(
        user.org_id, window_days=window_days
    )
    total_cost = sum(float(r["cost"]) for r in by_agent)
    return CostSummaryResponse(
        window_days=window_days,
        total_cost=total_cost,
        by_agent=[
            CostByAgent(agent_id=r["agent_id"], agent_name=r["agent_name"], cost=float(r["cost"]))
            for r in by_agent
        ],
        by_tool=[CostByTool(tool_name=r["tool_name"], cost=float(r["cost"])) for r in by_tool],
        estimated_savings_from_policy_enforcement=savings,
    )


@app.get("/command-center")
async def get_command_center_snapshot(
    user: AuthenticatedUser = require_any_role,
    window_days: int = 1,
) -> CommandCenterSnapshotResponse:
    """U16 (v2 upgrade), Command Center. "Availability" and "agents
    healthy" are both redefined to something this system actually tracks
    honestly -- see docs/adr/ADR-021. Polled by the frontend rather than
    pushed over a new WS channel -- the existing WS fan-out (ws.py) is
    scoped per-agent, and building a new org-wide broadcast channel for
    this one snapshot is real, separate scope, not attempted here."""
    agents = await db.list_agents_for_org(user.org_id)
    open_breaker_agents = await redis_bus.agents_with_open_circuit_breaker()
    agents_healthy = sum(1 for a in agents if a["id"] not in open_breaker_agents)

    availability = await db.get_availability_stats(user.org_id, window_days=window_days)
    completed = availability["completed"] or 0
    failed = availability["failed"] or 0
    availability_pct = 100 * completed / (completed + failed) if (completed + failed) else 100.0

    last_incident_at = await db.get_last_incident_at(user.org_id)
    recent = await db.get_recent_activity(user.org_id, limit=15)

    return CommandCenterSnapshotResponse(
        agents_total=len(agents),
        agents_healthy=agents_healthy,
        availability_pct=round(availability_pct, 2),
        window_days=window_days,
        last_incident_at=last_incident_at,
        recent_activity=[
            LiveActivityEntry(
                agent_id=r["agent_id"],
                agent_name=r["agent_name"],
                tool_name=r["tool_name"] or "unknown",
                decision=r["decision"],
                at=r["at"],
            )
            for r in recent
        ],
    )


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.port)


if __name__ == "__main__":
    run()
