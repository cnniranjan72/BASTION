"""Three-state circuit breaker per (agent_id, tool_name) — U6 (v2 upgrade),
UPGRADE_ARCHITECTURE.md §7. Protects the downstream systems agents call,
not the agents themselves (that's limits.py's job) — lives in the
interceptor's execution path right after policy evaluation has already
decided a call would otherwise be allowed, the last checkpoint before the
SDK is told to proceed. The interceptor never makes the real downstream
call itself (the SDK does, client-side — see docs/PROGRESS.md's documented
v1 deviation #2), so this breaker's only signal about outcomes comes from
POST /spans/{id}/complete, where main.py calls `record_success`/
`record_failure` after the SDK reports what actually happened.

State lives in Redis, not Postgres or in-process memory (ADR-015): breaker
state is operational/protective, not durable history — losing it on a
Redis restart just resets every breaker to CLOSED, a safe default, never a
correctness issue — and it must be shared across every interceptor replica
in production, since an in-process-only breaker would let each replica
independently keep hammering a downstream the others have already learned
is failing.

    CLOSED --(>= failure_threshold consecutive failures)--> OPEN
    OPEN --(open_timeout_seconds elapsed)--> HALF_OPEN
    HALF_OPEN --(any success)--> CLOSED
    HALF_OPEN --(any failure)--> OPEN (timer restarts)

Simplification, documented rather than silently assumed away: HALF_OPEN
doesn't restrict itself to exactly one in-flight trial call under truly
concurrent load (closing that window needs a Lua script or a distributed
lock) — every call is let through once HALF_OPEN, and whichever outcome
lands first decides the next transition. Acceptable at this system's scale
and out of scope to harden further this phase.
"""

from __future__ import annotations

import time
from uuid import UUID

import redis.asyncio as redis

DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_OPEN_TIMEOUT_SECONDS = 30.0
_FAILURE_COUNT_TTL_SECONDS = 300  # a stale failure streak decays rather than lingering forever


def _key(agent_id: UUID, tool_name: str, suffix: str) -> str:
    return f"bastion:breaker:{agent_id}:{tool_name}:{suffix}"


async def is_open(
    redis_client: redis.Redis,
    *,
    agent_id: UUID,
    tool_name: str,
    open_timeout_seconds: float = DEFAULT_OPEN_TIMEOUT_SECONDS,
) -> bool:
    """True if calls to this (agent, tool) should currently fail fast."""
    state = await redis_client.get(_key(agent_id, tool_name, "state"))
    if state != "OPEN":
        return False
    opened_at_raw = await redis_client.get(_key(agent_id, tool_name, "opened_at"))
    opened_at = float(opened_at_raw) if opened_at_raw is not None else 0.0
    if time.time() - opened_at >= open_timeout_seconds:
        # Timeout elapsed: transition to HALF_OPEN and let this call (and,
        # per the module docstring's documented simplification, any others
        # that land before the first outcome resolves it) probe the
        # downstream.
        await redis_client.set(_key(agent_id, tool_name, "state"), "HALF_OPEN")
        return False
    return True


async def record_success(redis_client: redis.Redis, *, agent_id: UUID, tool_name: str) -> None:
    await redis_client.delete(
        _key(agent_id, tool_name, "failures"),
        _key(agent_id, tool_name, "state"),
        _key(agent_id, tool_name, "opened_at"),
    )


async def record_failure(
    redis_client: redis.Redis,
    *,
    agent_id: UUID,
    tool_name: str,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
) -> None:
    state = await redis_client.get(_key(agent_id, tool_name, "state"))
    if state == "HALF_OPEN":
        # The trial probe failed — straight back to OPEN, timer restarts.
        await _open(redis_client, agent_id=agent_id, tool_name=tool_name)
        return
    failures_key = _key(agent_id, tool_name, "failures")
    failures = await redis_client.incr(failures_key)
    await redis_client.expire(failures_key, _FAILURE_COUNT_TTL_SECONDS)
    if failures >= failure_threshold:
        await _open(redis_client, agent_id=agent_id, tool_name=tool_name)


async def _open(redis_client: redis.Redis, *, agent_id: UUID, tool_name: str) -> None:
    await redis_client.set(_key(agent_id, tool_name, "state"), "OPEN")
    await redis_client.set(_key(agent_id, tool_name, "opened_at"), str(time.time()))
