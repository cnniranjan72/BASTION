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
from typing import Any
from uuid import UUID

import structlog
from bastion_shared import (
    EdgeAddedMessage,
    InvalidAccessToken,
    LiveNode,
    NodeAddedMessage,
    NodeUpdatedMessage,
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

from .config import config
from .db import db
from .graph import STATUS_FOR_EVENT_TYPE, fold_events_to_graph
from .human_auth import AuthenticatedUser, authenticate_user, decode_bearer_token
from .kafka_consumer import KafkaEventConsumer
from .logging import configure_logging, log
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
                node=LiveNode(
                    span_id=span_id, tool_name=payload.get("tool_name", ""), status="pending"
                )
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
    await kafka_consumer.start(_handle_notification)
    try:
        yield
    finally:
        await kafka_consumer.stop()
        await db.close()


app = FastAPI(title="bastion-aggregator", version="0.0.0", lifespan=lifespan)


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
        elapsed_ms = (time.perf_counter() - start) * 1000
        log.info(
            "request completed",
            status_code=response.status_code if response is not None else 500,
            elapsed_ms=round(elapsed_ms, 2),
        )
        if response is not None:
            response.headers["X-Request-Id"] = request_id
        structlog.contextvars.clear_contextvars()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "aggregator"}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
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
    try:
        while True:
            # No client->server protocol defined yet; just keep the
            # connection alive and detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(agent_id, websocket)


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
) -> list[TraceSummaryResponse]:
    """Persisted (terminal-state) traces only — an in-progress trace has no
    trace_summaries row yet by design (docs/ARCHITECTURE.md §14)."""
    records = await db.list_trace_summaries(user.org_id)
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


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.port)


if __name__ == "__main__":
    run()
