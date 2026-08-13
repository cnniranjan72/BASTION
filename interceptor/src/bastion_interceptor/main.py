"""Interceptor service — the latency-critical hot path (ARCHITECTURE.md §2.2).

Phase 1: POST /intercept, event emission (CallAttempted -> CallAllowed|
CallBlocked), and POST /spans/{id}/complete (CallCompleted|CallFailed) — see
API_SPEC.md for why completion is a separate call.

Phase 2: the hardcoded policy is replaced by the real YAML-shaped DSL
(policy.py), compiled and cached in-memory per policy_set_id, hot-reloaded
via Redis pub/sub (redis_bus.py) so a policy change takes effect on every
running interceptor instance with no restart (BUILD_PLAN.md Phase 2
milestone). Dashboard endpoints (POST/GET /policies, activate) are
unauthenticated for now — see docs/ARCHITECTURE.md §11.
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
    CreatePolicyRequest,
    EventType,
    InterceptAllowedResponse,
    InterceptBlockedResponse,
    InterceptRequest,
    PolicyDecisionPayload,
    PolicyResponse,
)
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import policy as policy_engine
from .auth import AuthenticatedAgent, authenticate_agent
from .config import config
from .db import db
from .logging import configure_logging, log
from .redis_bus import redis_bus

configure_logging()


async def _reload_policy_set(policy_set_id: UUID) -> None:
    """Redis pub/sub callback: re-fetch the (possibly new) active version
    for this set and refresh the cache — or evict if none is active."""
    record = await db.get_active_policy_for_set(policy_set_id)
    if record is None:
        policy_engine.policy_cache.evict(policy_set_id)
        return
    compiled = policy_engine.compile_policy_from_raw(
        record["id"], record["policy_set_id"], record["definition"]
    )
    policy_engine.policy_cache.put(compiled)
    log.info("policy cache reloaded", policy_set_id=str(policy_set_id), policy_id=str(record["id"]))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.connect()
    await redis_bus.connect()
    for record in await db.get_active_policies():
        compiled = policy_engine.compile_policy_from_raw(
            record["id"], record["policy_set_id"], record["definition"]
        )
        policy_engine.policy_cache.put(compiled)
    log.info("policy cache bootstrapped", count=len(policy_engine.policy_cache))
    await redis_bus.start_policy_listener(_reload_policy_set)
    try:
        yield
    finally:
        await redis_bus.close()
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

    compiled_policy = policy_engine.policy_cache.get(agent.default_policy_set_id)
    decision = policy_engine.evaluate(compiled_policy, body.tool_name, body.args)
    policy_id = compiled_policy.policy_id if compiled_policy is not None else None

    # require_approval isn't implemented until Phase 3 (the long-poll +
    # human-decision workflow). Fails closed rather than silently allowing
    # or half-implementing the flow — an unimplemented approval defaults to
    # deny, consistent with the product's own "block dangerous actions by
    # default" premise.
    if decision.action in ("block", "require_approval"):
        reason = decision.reason or "blocked by policy"
        if decision.action == "require_approval":
            reason = "approval required — approval flow lands in Phase 3, failing closed"
        await db.insert_event(
            trace_id=body.trace_id,
            span_id=span_id,
            parent_span_id=body.parent_span_id,
            agent_id=agent.id,
            event_type=EventType.CALL_BLOCKED,
            payload=PolicyDecisionPayload(
                policy_id=policy_id, decision="blocked", reason=reason
            ).model_dump(mode="json"),
        )
        log.info("call blocked", tool_name=body.tool_name, span_id=str(span_id), reason=reason)
        return InterceptBlockedResponse(span_id=span_id, policy_id=policy_id, reason=reason)

    await db.insert_event(
        trace_id=body.trace_id,
        span_id=span_id,
        parent_span_id=body.parent_span_id,
        agent_id=agent.id,
        event_type=EventType.CALL_ALLOWED,
        payload=PolicyDecisionPayload(policy_id=policy_id, decision="allowed").model_dump(
            mode="json"
        ),
    )
    log.info("call allowed", tool_name=body.tool_name, span_id=str(span_id))
    return InterceptAllowedResponse(span_id=span_id, policy_id=policy_id, result=None)


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


def _policy_response(record: Any) -> PolicyResponse:
    return PolicyResponse(
        id=record["id"],
        org_id=record["org_id"],
        policy_set_id=record["policy_set_id"],
        name=record["name"],
        version=record["version"],
        definition=record["definition"],
        active=record["active"],
        created_at=record["created_at"],
    )


@app.post("/policies", status_code=201)
async def create_policy(body: CreatePolicyRequest, request: Request) -> PolicyResponse:
    # Compiled (not just Pydantic-validated) immediately, even though this
    # version isn't active yet — a condition expression outside the safe
    # subset (policy.py's PolicyConditionError) should fail the create call,
    # not silently wait to blow up the first time this version is activated.
    try:
        policy_engine.compile_policy(uuid.uuid4(), uuid.uuid4(), body.definition)
    except policy_engine.PolicyConditionError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_POLICY_CONDITION",
                    "message": str(exc),
                    "request_id": request.state.request_id,
                }
            },
        ) from exc
    definition_dump = [rule.model_dump(mode="json") for rule in body.definition]
    record = await db.create_policy(org_id=body.org_id, name=body.name, definition=definition_dump)
    return _policy_response(record)


@app.get("/policies")
async def list_policies(org_id: UUID) -> list[PolicyResponse]:
    records = await db.list_policies(org_id)
    return [_policy_response(r) for r in records]


@app.post("/policies/{policy_id}/activate")
async def activate_policy(policy_id: UUID, request: Request) -> PolicyResponse:
    record = await db.activate_policy(policy_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "POLICY_NOT_FOUND",
                    "message": f"no policy {policy_id}",
                    "request_id": request.state.request_id,
                }
            },
        )
    compiled = policy_engine.compile_policy_from_raw(
        record["id"], record["policy_set_id"], record["definition"]
    )
    policy_engine.policy_cache.put(compiled)
    # This instance's cache is already updated (above); publish so every
    # *other* running interceptor instance picks it up too, with no restart.
    await redis_bus.publish_policy_update(record["policy_set_id"])
    return _policy_response(record)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.port)


if __name__ == "__main__":
    run()
