"""Aggregator service — event-stream subscriber, graph builder, WS fan-out
(ARCHITECTURE.md §2.5).

Phase 4: subscribes to `events` via Postgres LISTEN/NOTIFY (listener.py),
folds each notified trace's events into a TraceGraph (graph.py) — the same
fold GET /traces/{id} uses on demand — and persists trace_summaries once a
trace reaches a terminal state (docs/ARCHITECTURE.md §14). WebSocket fan-out
lands in Phase 6.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import structlog
from bastion_shared import TraceGraph, TraceSummaryResponse
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import config
from .db import db
from .graph import fold_events_to_graph
from .listener import EventListener
from .logging import configure_logging, log

configure_logging()

listener = EventListener()
# Live in-memory tracking (ARCHITECTURE.md §2.5) — updated on every
# notification, evicted once a trace is persisted to trace_summaries and no
# longer "active." Not the source of truth (events/trace_summaries are);
# this is scaffolding Phase 6's WS fan-out will read from directly.
active_traces: dict[UUID, TraceGraph] = {}


async def _handle_notification(data: dict[str, str]) -> None:
    trace_id = UUID(data["trace_id"])
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


@app.get("/traces/{trace_id}")
async def get_trace(trace_id: UUID, request: Request) -> TraceGraph:
    """Full replay: trace_summaries.graph_snapshot if a persisted projection
    exists (fast path), else folds `events` fresh — covers active/in-progress
    traces and demonstrates the event-sourcing discipline (current state is
    always derivable from `events`, the cache is a pure accelerator, not the
    source of truth — CLAUDE.md rule #1)."""
    summary = await db.get_trace_summary(trace_id)
    if summary is not None:
        return TraceGraph.model_validate(summary["graph_snapshot"])

    if trace_id in active_traces:
        return active_traces[trace_id]

    events = await db.get_events_for_trace(trace_id)
    if not events:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "TRACE_NOT_FOUND",
                    "message": f"no trace {trace_id}",
                    "request_id": request.state.request_id,
                }
            },
        )
    return fold_events_to_graph(events)


@app.get("/traces/{trace_id}/events")
async def get_trace_events(trace_id: UUID, request: Request) -> list[dict[str, Any]]:
    """Raw event list, for the 2D inspector panel (ARCHITECTURE.md §2.6)."""
    events = await db.get_events_for_trace(trace_id)
    if not events:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "TRACE_NOT_FOUND",
                    "message": f"no trace {trace_id}",
                    "request_id": request.state.request_id,
                }
            },
        )
    return [dict(e) for e in events]


@app.get("/traces")
async def list_traces(org_id: UUID) -> list[TraceSummaryResponse]:
    """Persisted (terminal-state) traces only — an in-progress trace has no
    trace_summaries row yet by design (docs/ARCHITECTURE.md §14)."""
    records = await db.list_trace_summaries(org_id)
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
