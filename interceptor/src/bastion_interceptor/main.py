"""Interceptor service — the latency-critical hot path (ARCHITECTURE.md §2.2).

Phase 1: POST /intercept with a hardcoded policy (policy.py), event emission
(CallAttempted -> CallAllowed|CallBlocked), and POST /spans/{id}/complete
(CallCompleted|CallFailed) — see API_SPEC.md for why completion is a
separate call. The policy DSL + hot reload land in Phase 2.
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
    CallAttemptedPayload,
    CallOutcomePayload,
    CompleteSpanRequest,
    CompleteSpanResponse,
    EventType,
    InterceptAllowedResponse,
    InterceptBlockedResponse,
    InterceptRequest,
    PolicyDecisionPayload,
)
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import policy as policy_engine
from .auth import AuthenticatedAgent, authenticate_agent
from .config import config
from .db import db
from .logging import configure_logging, log

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.connect()
    try:
        yield
    finally:
        await db.close()


app = FastAPI(title="bastion-interceptor", version="0.0.0", lifespan=lifespan)


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
    return {"status": "ok", "service": "interceptor"}


@app.post("/intercept")
async def intercept(
    body: InterceptRequest,
    request: Request,
    agent: AuthenticatedAgent = Depends(authenticate_agent),
) -> InterceptAllowedResponse | InterceptBlockedResponse:
    if body.agent_id != agent.id:
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "AGENT_MISMATCH",
                    "message": "request agent_id does not match the authenticated agent",
                    "request_id": request.state.request_id,
                }
            },
        )

    span_id = uuid.uuid4()

    await db.insert_event(
        trace_id=body.trace_id,
        span_id=span_id,
        parent_span_id=body.parent_span_id,
        agent_id=agent.id,
        event_type=EventType.CALL_ATTEMPTED,
        payload=CallAttemptedPayload(tool_name=body.tool_name, args=body.args).model_dump(),
    )

    decision = policy_engine.evaluate(body.tool_name, body.args)

    if decision.action == "block":
        await db.insert_event(
            trace_id=body.trace_id,
            span_id=span_id,
            parent_span_id=body.parent_span_id,
            agent_id=agent.id,
            event_type=EventType.CALL_BLOCKED,
            payload=PolicyDecisionPayload(
                policy_id=None, decision="blocked", reason=decision.reason
            ).model_dump(),
        )
        log.info(
            "call blocked", tool_name=body.tool_name, span_id=str(span_id), reason=decision.reason
        )
        return InterceptBlockedResponse(
            span_id=span_id, policy_id=None, reason=decision.reason or "blocked by policy"
        )

    await db.insert_event(
        trace_id=body.trace_id,
        span_id=span_id,
        parent_span_id=body.parent_span_id,
        agent_id=agent.id,
        event_type=EventType.CALL_ALLOWED,
        payload=PolicyDecisionPayload(policy_id=None, decision="allowed").model_dump(),
    )
    log.info("call allowed", tool_name=body.tool_name, span_id=str(span_id))
    return InterceptAllowedResponse(span_id=span_id, policy_id=None, result=None)


@app.post("/spans/{span_id}/complete")
async def complete_span(
    span_id: UUID,
    body: CompleteSpanRequest,
    request: Request,
    agent: AuthenticatedAgent = Depends(authenticate_agent),
) -> CompleteSpanResponse:
    span = await db.get_span_decision(span_id)
    if span is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "SPAN_NOT_FOUND",
                    "message": f"no decided span {span_id}",
                    "request_id": request.state.request_id,
                }
            },
        )
    if span["agent_id"] != agent.id:
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "AGENT_MISMATCH",
                    "message": "span belongs to a different agent",
                    "request_id": request.state.request_id,
                }
            },
        )
    if span["event_type"] != EventType.CALL_ALLOWED.value:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "SPAN_NOT_ALLOWED",
                    "message": "only allowed spans can be completed",
                    "request_id": request.state.request_id,
                }
            },
        )

    event_type = EventType.CALL_COMPLETED if body.status == "completed" else EventType.CALL_FAILED
    await db.insert_event(
        trace_id=span["trace_id"],
        span_id=span_id,
        parent_span_id=span["parent_span_id"],
        agent_id=agent.id,
        event_type=event_type,
        payload=CallOutcomePayload(
            latency_ms=body.latency_ms, cost=body.cost, result=body.result, error=body.error
        ).model_dump(),
    )
    return CompleteSpanResponse(span_id=span_id, status=body.status)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.port)


if __name__ == "__main__":
    run()
