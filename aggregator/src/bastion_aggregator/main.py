"""Aggregator service — event-stream subscriber, graph builder, WS fan-out
(ARCHITECTURE.md §2.5).

Phase 4: subscribes to `events` via Postgres LISTEN/NOTIFY (listener.py),
folds each notified trace's events into a TraceGraph (graph.py) — the same
fold GET /traces/{id} uses on demand — and persists trace_summaries once a
trace reaches a terminal state (docs/ARCHITECTURE.md §14).

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

from .config import config
from .db import db
from .graph import fold_events_to_graph
from .human_auth import AuthenticatedUser, authenticate_user, decode_bearer_token
from .listener import EventListener
from .logging import configure_logging, log
from .ws import manager as ws_manager

configure_logging()

require_any_role = Depends(authenticate_user)

listener = EventListener()
# Live in-memory tracking (ARCHITECTURE.md §2.5) — updated on every
# notification, evicted once a trace is persisted to trace_summaries and no
# longer "active." Not the source of truth (events/trace_summaries are);
# also what WS /live/{agent_id} deltas are pushed from (below).
active_traces: dict[UUID, TraceGraph] = {}

# Events whose node is brand new vs. an update to an existing one — decides
# node_added (+ edge_added if it has a parent) vs. node_updated.
_CREATION_EVENT = "CallAttempted"


async def _handle_notification(data: dict[str, str]) -> None:
    trace_id = UUID(data["trace_id"])
    span_id = UUID(data["span_id"])
    event_type = data["event_type"]

    events = await db.get_events_for_trace(trace_id)
    if not events:
        return
    graph = fold_events_to_graph(events)

    node = next((n for n in graph.nodes if n.span_id == span_id), None)
    if node is not None:
        if event_type == _CREATION_EVENT:
            await ws_manager.broadcast(
                graph.agent_id,
                NodeAddedMessage(
                    node=LiveNode(span_id=node.span_id, tool_name=node.tool_name, status="pending")
                ),
            )
            if node.parent_span_id is not None:
                await ws_manager.broadcast(
                    graph.agent_id,
                    EdgeAddedMessage.model_validate(
                        {"from": node.parent_span_id, "to": node.span_id}
                    ),
                )
        elif node.status != "pending":
            # Only CallAttempted (handled above) ever sets "pending"; every
            # other event type transitions a node past it, which is exactly
            # NodeUpdatedMessage's narrower status literal (no "pending").
            await ws_manager.broadcast(
                graph.agent_id,
                NodeUpdatedMessage(
                    span_id=node.span_id,
                    status=node.status,
                    latency_ms=node.latency_ms,
                    cost=node.cost,
                    reason=node.reason,
                ),
            )

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
    await listener.start(_handle_notification)
    try:
        yield
    finally:
        await listener.stop()
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


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422, content=_error_body(request, "VALIDATION_ERROR", str(exc.errors()))
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
