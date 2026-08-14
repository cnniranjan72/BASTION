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

Phase 3: require_approval creates an approval_requests row and returns
pending_approval immediately — /intercept itself never blocks for a
human-timescale decision (that would break the stateless, horizontally
scalable hot-path story). GET /approvals/{id} is the actual long-poll
target the SDK calls in a loop; it's woken by a Redis pub/sub signal rather
than busy-polling Postgres, but Postgres is still the source of truth for
what happened. See docs/ARCHITECTURE.md's approval-flow section.

Phase 5: real auth per AUTH.md — argon2id passwords, Ed25519-signed JWT
access tokens, refresh token rotation with family-based reuse detection
(POST /auth/refresh), RBAC. Every dashboard endpoint (policies, approvals)
now requires an authenticated user and derives org scoping from the JWT
instead of the Phase 2-4 explicit `org_id` param stopgap.
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import asyncpg
import structlog
from bastion_shared import (
    AccessTokenClaims,
    AgentResponse,
    ApiTokenResponse,
    ApprovalRequestResponse,
    CallAttemptedPayload,
    CallOutcomePayload,
    ChangePasswordRequest,
    CompleteSpanRequest,
    CompleteSpanResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    CreateApiTokenRequest,
    CreateApiTokenResponse,
    CreatePolicyRequest,
    CreateUserRequest,
    CreateUserResponse,
    EventType,
    InterceptAllowedResponse,
    InterceptBlockedResponse,
    InterceptPendingResponse,
    InterceptRequest,
    LoginRequest,
    LogoutRequest,
    PolicyDecisionPayload,
    PolicyResponse,
    RefreshRequest,
    SignupRequest,
    TokenPairResponse,
    UpdateAgentRequest,
    UpdateUserRoleRequest,
    UserResponse,
    UserRole,
    encode_access_token,
)
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import policy as policy_engine
from .auth import AuthenticatedAgent, authenticate_agent, hash_api_key
from .config import config
from .db import db
from .human_auth import (
    API_TOKEN_PREFIX,
    AuthenticatedUser,
    hash_api_token,
    hash_password,
    require_role,
    verify_password,
)
from .logging import configure_logging, log
from .metrics import intercept_latency_seconds, policy_decisions_total
from .redis_bus import redis_bus

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
ALL_ROLES: tuple[UserRole, ...] = ("owner", "admin", "approver", "viewer")
REFRESH_TOKEN_BYTES = 32

configure_logging()

# Named singletons rather than calling require_role(...) inline in argument
# defaults — ruff's B008 (mutable-default-style check) flags nested calls
# even inside an allow-listed Depends(...), and re-creating the closure on
# every request has no benefit anyway.
require_admin = Depends(require_role("owner", "admin"))
require_approver = Depends(require_role("owner", "admin", "approver"))
require_any_role = Depends(require_role(*ALL_ROLES))


@lru_cache(maxsize=1)
def _private_key_pem() -> str:
    with open(config.jwt_private_key_path, encoding="utf-8") as f:
        return f.read()


def _hash_refresh_token(raw_token: str) -> str:
    # Same reasoning as agent API keys (auth.py): a high-entropy random
    # token is a lookup key, not a password — no need for argon2id here.
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _issue_token_pair(user: Any, family_id: UUID) -> tuple[TokenPairResponse, str]:
    """Returns (response, new_refresh_token_hash) — caller persists the hash."""
    raw_refresh = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    access_token = encode_access_token(
        AccessTokenClaims(user_id=user["id"], org_id=user["org_id"], role=user["role"]),
        _private_key_pem(),
    )
    response = TokenPairResponse(
        access_token=access_token, refresh_token=raw_refresh, role=user["role"]
    )
    return response, _hash_refresh_token(raw_refresh)


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


def _format_validation_errors(exc: RequestValidationError) -> str:
    # exc.errors() is a list of {"loc": ("body", "email"), "msg": "...", ...}
    # dicts — str()'ing the whole thing (the previous behavior) dumped a raw
    # Python repr straight into the error message shown to users, e.g. in
    # the signup form. "field: message", joined, is what a human wants.
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
    return {"status": "ok", "service": "interceptor"}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/auth/signup", status_code=201)
async def signup(body: SignupRequest, request: Request) -> TokenPairResponse:
    """Self-serve: creates a brand-new org + its first user (role owner),
    then logs them in immediately — same token-issuing path as /auth/login,
    just with a fresh account instead of an existing one. No invite flow;
    every signup is a new org, never a join of an existing one (AUTH.md/
    API_SPEC.md never specced either, and joining an org via a bare email+
    password with no invite token would be a real security hole — anyone
    could add themselves to any org they knew the name of)."""
    try:
        user = await db.create_org_and_owner(
            org_name=body.org_name, email=body.email, password_hash=hash_password(body.password)
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "EMAIL_ALREADY_REGISTERED",
                    "message": "an account with this email already exists",
                    "request_id": request.state.request_id,
                }
            },
        ) from exc

    family_id = uuid.uuid4()
    response, token_hash = _issue_token_pair(user, family_id)
    expires_at = datetime.now(UTC) + timedelta(days=config.refresh_token_ttl_days)
    await db.insert_refresh_token(
        user_id=user["id"], token_hash=token_hash, family_id=family_id, expires_at=expires_at
    )
    log.info("signup", user_id=str(user["id"]), org_id=str(user["org_id"]))
    return response


@app.post("/auth/login")
async def login(body: LoginRequest, request: Request) -> TokenPairResponse:
    user = await db.get_user_by_email(body.email)
    if user is None or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "INVALID_CREDENTIALS",
                    "message": "invalid email or password",
                    "request_id": request.state.request_id,
                }
            },
        )
    family_id = uuid.uuid4()
    response, token_hash = _issue_token_pair(user, family_id)
    expires_at = datetime.now(UTC) + timedelta(days=config.refresh_token_ttl_days)
    await db.insert_refresh_token(
        user_id=user["id"], token_hash=token_hash, family_id=family_id, expires_at=expires_at
    )
    return response


@app.post("/auth/refresh")
async def refresh(body: RefreshRequest, request: Request) -> TokenPairResponse:
    """The reuse-detection mechanism AUTH.md §2 calls "the part interviewers
    actually probe on": every refresh token is one-time-use. If the token
    presented here isn't the current unrevoked token for its family — i.e.
    it was already rotated away (or the family was already revoked) — that
    can only mean it leaked and is being used in parallel with the
    legitimate client. The response to that signal is to revoke the
    *entire* family, not just reject this one request."""
    presented_hash = _hash_refresh_token(body.refresh_token)
    record = await db.get_refresh_token_by_hash(presented_hash)
    if record is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "INVALID_REFRESH_TOKEN",
                    "message": "refresh token not recognized",
                    "request_id": request.state.request_id,
                }
            },
        )
    if record["revoked_at"] is not None:
        await db.revoke_refresh_token_family(record["family_id"])
        log.warning(
            "refresh token reuse detected, family revoked", family_id=str(record["family_id"])
        )
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "REFRESH_TOKEN_REUSED",
                    "message": (
                        "token reuse detected — all sessions in this family "
                        "were revoked, log in again"
                    ),
                    "request_id": request.state.request_id,
                }
            },
        )
    if record["expires_at"] < datetime.now(UTC):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "REFRESH_TOKEN_EXPIRED",
                    "message": "refresh token expired, log in again",
                    "request_id": request.state.request_id,
                }
            },
        )

    user = await db.get_user_by_id(record["user_id"])
    assert user is not None
    response, new_hash = _issue_token_pair(user, record["family_id"])
    new_expires_at = datetime.now(UTC) + timedelta(days=config.refresh_token_ttl_days)
    await db.rotate_refresh_token(
        old_token_id=record["id"],
        user_id=user["id"],
        family_id=record["family_id"],
        new_token_hash=new_hash,
        expires_at=new_expires_at,
    )
    return response


@app.post("/auth/logout")
async def logout(body: LogoutRequest) -> dict[str, str]:
    record = await db.get_refresh_token_by_hash(_hash_refresh_token(body.refresh_token))
    if record is not None:
        await db.revoke_refresh_token_family(record["family_id"])
    return {"status": "logged_out"}


@app.patch("/auth/password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: AuthenticatedUser = require_any_role,
) -> dict[str, str]:
    record = await db.get_user_by_id(user.id)
    assert record is not None
    if not verify_password(body.current_password, record["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "INVALID_CURRENT_PASSWORD",
                    "message": "current password is incorrect",
                    "request_id": request.state.request_id,
                }
            },
        )
    await db.update_user_password(user.id, hash_password(body.new_password))
    log.info("password changed", user_id=str(user.id))
    return {"status": "password_changed"}


@app.get("/approvals-ui", include_in_schema=False)
async def approvals_ui() -> FileResponse:
    """Plain HTML/JS approver page (BUILD_PLAN.md Phase 3 — "not the 3D
    view yet"). Dev/demo tool — the underlying API now requires a real
    access token (Phase 5); the page has a token field, see approvals.html."""
    return FileResponse(STATIC_DIR / "approvals.html")


@app.post("/intercept")
async def intercept(
    body: InterceptRequest,
    request: Request,
    agent: AuthenticatedAgent = Depends(authenticate_agent),
) -> InterceptAllowedResponse | InterceptBlockedResponse | InterceptPendingResponse:
    with intercept_latency_seconds.time():
        return await _intercept(body, request, agent)


async def _intercept(
    body: InterceptRequest,
    request: Request,
    agent: AuthenticatedAgent,
) -> InterceptAllowedResponse | InterceptBlockedResponse | InterceptPendingResponse:
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

    if decision.action == "require_approval":
        await db.insert_event(
            trace_id=body.trace_id,
            span_id=span_id,
            parent_span_id=body.parent_span_id,
            agent_id=agent.id,
            event_type=EventType.CALL_PENDING_APPROVAL,
            payload=PolicyDecisionPayload(
                policy_id=policy_id, decision="pending_approval", reason=decision.reason
            ).model_dump(mode="json"),
        )
        approval = await db.insert_approval_request(trace_id=body.trace_id, span_id=span_id)
        log.info("call pending approval", tool_name=body.tool_name, span_id=str(span_id))
        policy_decisions_total.labels(decision="pending_approval").inc()
        return InterceptPendingResponse(
            span_id=span_id,
            approval_request_id=approval["id"],
            poll_url=f"/approvals/{approval['id']}",
        )

    if decision.action == "block":
        reason = decision.reason or "blocked by policy"
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
        policy_decisions_total.labels(decision="blocked").inc()
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
    policy_decisions_total.labels(decision="allowed").inc()
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
    # ApprovalGranted is the "allowed" outcome for a span resolved via the
    # approval flow (Phase 3) — it never gets its own separate CallAllowed.
    if span["event_type"] not in (EventType.CALL_ALLOWED.value, EventType.APPROVAL_GRANTED.value):
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
async def create_policy(
    body: CreatePolicyRequest,
    request: Request,
    user: AuthenticatedUser = require_admin,
) -> PolicyResponse:
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
    record = await db.create_policy(org_id=user.org_id, name=body.name, definition=definition_dump)
    return _policy_response(record)


@app.get("/policies")
async def list_policies(
    user: AuthenticatedUser = require_any_role,
) -> list[PolicyResponse]:
    records = await db.list_policies(user.org_id)
    return [_policy_response(r) for r in records]


@app.post("/policies/{policy_id}/activate")
async def activate_policy(
    policy_id: UUID,
    request: Request,
    user: AuthenticatedUser = require_admin,
) -> PolicyResponse:
    record = await db.activate_policy(policy_id, user.org_id)
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


def _agent_response(record: Any) -> AgentResponse:
    return AgentResponse(
        id=record["id"],
        org_id=record["org_id"],
        name=record["name"],
        policy_set_id=record["default_policy_set_id"],
        created_at=record["created_at"],
    )


@app.post("/agents", status_code=201)
async def create_agent(
    body: CreateAgentRequest,
    request: Request,
    user: AuthenticatedUser = require_admin,
) -> CreateAgentResponse:
    if body.policy_set_id is not None and not await db.policy_set_belongs_to_org(
        body.policy_set_id, user.org_id
    ):
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "POLICY_SET_NOT_FOUND",
                    "message": f"no policy set {body.policy_set_id}",
                    "request_id": request.state.request_id,
                }
            },
        )
    # bastion_ prefix makes a leaked key greppable/identifiable in logs —
    # same reasoning as e.g. GitHub's sk-/ghp_ prefixes. Only ever shown
    # here, at creation; the DB stores nothing but its SHA-256 hash
    # (auth.py — same "lookup key, not a password" reasoning as every
    # other agent API key).
    raw_key = f"bastion_{secrets.token_urlsafe(32)}"
    record = await db.create_agent(
        org_id=user.org_id,
        name=body.name,
        api_key_hash=hash_api_key(raw_key),
        policy_set_id=body.policy_set_id,
    )
    log.info("agent created", agent_id=str(record["id"]), org_id=str(user.org_id))
    return CreateAgentResponse(**_agent_response(record).model_dump(), api_key=raw_key)


@app.get("/agents")
async def list_agents(user: AuthenticatedUser = require_any_role) -> list[AgentResponse]:
    records = await db.list_agents(user.org_id)
    return [_agent_response(r) for r in records]


@app.patch("/agents/{agent_id}")
async def update_agent(
    agent_id: UUID,
    body: UpdateAgentRequest,
    request: Request,
    user: AuthenticatedUser = require_admin,
) -> AgentResponse:
    if body.policy_set_id is not None and not await db.policy_set_belongs_to_org(
        body.policy_set_id, user.org_id
    ):
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "POLICY_SET_NOT_FOUND",
                    "message": f"no policy set {body.policy_set_id}",
                    "request_id": request.state.request_id,
                }
            },
        )
    record = await db.update_agent_policy_set(agent_id, user.org_id, body.policy_set_id)
    if record is None:
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
    return _agent_response(record)


def _user_response(record: Any) -> UserResponse:
    return UserResponse(
        id=record["id"],
        org_id=record["org_id"],
        email=record["email"],
        role=record["role"],
        created_at=record["created_at"],
    )


@app.get("/users")
async def list_users(user: AuthenticatedUser = require_any_role) -> list[UserResponse]:
    records = await db.list_users_for_org(user.org_id)
    return [_user_response(r) for r in records]


@app.post("/users", status_code=201)
async def create_user(
    body: CreateUserRequest,
    request: Request,
    user: AuthenticatedUser = require_admin,
) -> CreateUserResponse:
    # Provisioning, not an email invite — no email-sending infrastructure
    # exists in this project (see users_api.py's module docstring). The
    # temporary password is shown exactly once, same one-time-reveal
    # pattern as an agent's API key.
    temporary_password = secrets.token_urlsafe(12)
    try:
        record = await db.create_user(
            org_id=user.org_id,
            email=body.email,
            password_hash=hash_password(temporary_password),
            role=body.role,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "EMAIL_ALREADY_REGISTERED",
                    "message": "an account with this email already exists",
                    "request_id": request.state.request_id,
                }
            },
        ) from exc
    log.info("teammate provisioned", user_id=str(record["id"]), org_id=str(user.org_id))
    return CreateUserResponse(
        **_user_response(record).model_dump(), temporary_password=temporary_password
    )


@app.patch("/users/{user_id}/role")
async def update_user_role(
    user_id: UUID,
    body: UpdateUserRoleRequest,
    request: Request,
    user: AuthenticatedUser = require_admin,
) -> UserResponse:
    target = await db.get_user_by_id(user_id)
    if target is None or target["org_id"] != user.org_id:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "USER_NOT_FOUND",
                    "message": f"no user {user_id}",
                    "request_id": request.state.request_id,
                }
            },
        )
    # An org that hits zero owners can never activate a policy, provision a
    # teammate, or promote anyone back to owner again — a self-inflicted
    # lockout, not just a permissions edge case. Block the specific demotion
    # that would cause it, not demotion in general.
    if target["role"] == "owner" and body.role != "owner":
        owner_count = await db.count_owners_for_org(user.org_id)
        if owner_count <= 1:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "LAST_OWNER",
                        "message": "cannot demote the organization's last owner",
                        "request_id": request.state.request_id,
                    }
                },
            )
    record = await db.update_user_role(user_id, user.org_id, body.role)
    assert record is not None
    return _user_response(record)


def _api_token_response(record: Any) -> ApiTokenResponse:
    return ApiTokenResponse(
        id=record["id"],
        name=record["name"],
        token_prefix=record["token_prefix"],
        created_at=record["created_at"],
        last_used_at=record["last_used_at"],
        revoked_at=record["revoked_at"],
    )


@app.get("/api-tokens")
async def list_api_tokens(user: AuthenticatedUser = require_any_role) -> list[ApiTokenResponse]:
    records = await db.list_api_tokens_for_user(user.id)
    return [_api_token_response(r) for r in records]


@app.post("/api-tokens", status_code=201)
async def create_api_token(
    body: CreateApiTokenRequest, user: AuthenticatedUser = require_any_role
) -> CreateApiTokenResponse:
    # A long-lived credential for scripts/CI to call this same management
    # API — same auth domain as a login session (authenticate_user accepts
    # either), not a separate/weaker path. Shown once, same one-time-reveal
    # pattern as an agent's key and a provisioned teammate's temp password.
    raw_token = f"{API_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    record = await db.create_api_token(
        org_id=user.org_id,
        user_id=user.id,
        name=body.name,
        token_prefix=raw_token[: len(API_TOKEN_PREFIX) + 6],
        token_hash=hash_api_token(raw_token),
    )
    log.info("api token created", user_id=str(user.id), org_id=str(user.org_id))
    return CreateApiTokenResponse(**_api_token_response(record).model_dump(), token=raw_token)


@app.delete("/api-tokens/{token_id}", status_code=204)
async def revoke_api_token(
    token_id: UUID, request: Request, user: AuthenticatedUser = require_any_role
) -> Response:
    record = await db.revoke_api_token(token_id, user.id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "API_TOKEN_NOT_FOUND",
                    "message": f"no active API token {token_id}",
                    "request_id": request.state.request_id,
                }
            },
        )
    return Response(status_code=204)


def _approval_response(record: Any) -> ApprovalRequestResponse:
    return ApprovalRequestResponse(
        id=record["id"],
        trace_id=record["trace_id"],
        span_id=record["span_id"],
        status=record["status"],
        requested_at=record["requested_at"],
        resolved_by=record["resolved_by"],
        resolved_at=record["resolved_at"],
    )


_APPROVAL_EVENT_DECISION: dict[EventType, Literal["allowed", "blocked"]] = {
    EventType.APPROVAL_GRANTED: "allowed",
    EventType.APPROVAL_DENIED: "blocked",
}


async def _emit_approval_resolution_event(
    record: Any, event_type: EventType, reason: str | None
) -> None:
    lineage = await db.get_span_lineage(record["span_id"])
    if lineage is None:
        return
    await db.insert_event(
        trace_id=record["trace_id"],
        span_id=record["span_id"],
        parent_span_id=lineage["parent_span_id"],
        agent_id=lineage["agent_id"],
        event_type=event_type,
        payload=PolicyDecisionPayload(
            policy_id=None, decision=_APPROVAL_EVENT_DECISION[event_type], reason=reason
        ).model_dump(mode="json"),
    )


@app.get("/approvals/{approval_id}")
async def get_approval(
    approval_id: UUID,
    request: Request,
    agent: AuthenticatedAgent = Depends(authenticate_agent),
) -> ApprovalRequestResponse:
    """The SDK's actual long-poll target (not /intercept — see module
    docstring). Blocks up to config.approval_long_poll_seconds if still
    pending, woken early by a Redis signal from approve/deny; the SDK calls
    this in a loop until the status is no longer "pending" or its own
    overall budget elapses (docs/ARCHITECTURE.md's approval-flow section)."""
    record = await db.get_approval_request(approval_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "APPROVAL_NOT_FOUND",
                    "message": f"no approval {approval_id}",
                    "request_id": request.state.request_id,
                }
            },
        )

    lineage = await db.get_span_lineage(record["span_id"])
    if lineage is None or lineage["agent_id"] != agent.id:
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "AGENT_MISMATCH",
                    "message": "approval belongs to a different agent",
                    "request_id": request.state.request_id,
                }
            },
        )

    if record["status"] == "pending":
        # Result deliberately ignored: whether this returns True (woken by
        # a signal) or False (timed out), Postgres below is re-checked as
        # the source of truth either way — see redis_bus.wait_for_approval_signal.
        await redis_bus.wait_for_approval_signal(approval_id, config.approval_long_poll_seconds)
        expired = await db.expire_stale_approval(approval_id, config.approval_ttl_seconds)
        if expired is not None:
            await _emit_approval_resolution_event(
                expired, EventType.APPROVAL_DENIED, "approval timed out"
            )
            record = expired
        else:
            refreshed = await db.get_approval_request(approval_id)
            assert refreshed is not None
            record = refreshed

    return _approval_response(record)


@app.get("/approvals")
async def list_pending_approvals(
    user: AuthenticatedUser = require_any_role,
) -> list[ApprovalRequestResponse]:
    records = await db.list_pending_approvals_for_org(user.org_id)
    return [_approval_response(r) for r in records]


async def _resolve_approval(
    approval_id: UUID, status: str, request: Request, user: AuthenticatedUser
) -> ApprovalRequestResponse:
    record = await db.resolve_approval(
        approval_id, status=status, resolved_by=user.id, org_id=user.org_id
    )
    if record is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "APPROVAL_NOT_PENDING",
                    "message": (
                        f"approval {approval_id} is not pending "
                        "(already resolved, doesn't exist, or belongs to a different org)"
                    ),
                    "request_id": request.state.request_id,
                }
            },
        )
    event_type = EventType.APPROVAL_GRANTED if status == "approved" else EventType.APPROVAL_DENIED
    await _emit_approval_resolution_event(record, event_type, None)
    await redis_bus.publish_approval_resolved(approval_id)
    return _approval_response(record)


@app.post("/approvals/{approval_id}/approve")
async def approve_approval(
    approval_id: UUID,
    request: Request,
    user: AuthenticatedUser = require_approver,
) -> ApprovalRequestResponse:
    return await _resolve_approval(approval_id, "approved", request, user)


@app.post("/approvals/{approval_id}/deny")
async def deny_approval(
    approval_id: UUID,
    request: Request,
    user: AuthenticatedUser = require_approver,
) -> ApprovalRequestResponse:
    return await _resolve_approval(approval_id, "denied", request, user)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.port)


if __name__ == "__main__":
    run()
