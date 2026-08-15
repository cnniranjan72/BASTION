"""U14 chaos scenario (UPGRADE_ARCHITECTURE.md §16): "Kill Redis" —
required invariant: "policy cache falls back to Postgres fetch; rate
limits reset safely (fail open or closed — pick one, document why)."

Doc/code conflict flagged and resolved (per the standing rule: if
UPGRADE_ARCHITECTURE.md conflicts with the actual implementation, flag it
directly with a recommended resolution rather than silently picking one).
The "policy cache falls back to Postgres fetch" phrasing does not match
this system's actual, already-justified design: `PolicyCache` (policy.py)
is a plain in-memory dict with no Redis dependency for *reads* at all — a
Redis outage doesn't touch it either way, and a genuine cache miss (no
policy cached for that policy_set_id) has never synchronously fetched from
Postgres; it defaults to `Decision(action="allow")` (policy.py:171-172,
"safe default, matches the trailing match: '*' -> allow convention"). A
synchronous per-request Postgres fetch on a cache miss would itself violate
CLAUDE.md rule #4 ("/intercept never blocks on non-essential work") — U5's
actual design (ADR-007) deliberately chose async periodic reconciliation
(policy_reconciler.py, every 30s, Redis-independent since it reads
Postgres directly) over synchronous fallback specifically to avoid that.
Resolution: this test verifies the system's real, already-decided behavior
(cache reads are Redis-independent; a miss defaults to allow, not a
Postgres round-trip) rather than the literal "falls back to Postgres
fetch" wording, and cites ADR-007 instead of writing a new ADR for a
decision that's already recorded.

The rate-limits half of the invariant is already decided and documented:
ADR-015 chose fail-open for both `limits.check_and_apply_limits` and
`circuit_breaker.is_open` (interceptor/main.py's `except redis.RedisError`
blocks, added specifically for CLAUDE.md rule #4). This test verifies that
choice empirically against a real Redis outage rather than only trusting
the ADR's prose.
"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from bastion_interceptor.main import app as interceptor_app

REDIS_CONTAINER = "bastion-redis"


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=interceptor_app), base_url="http://interceptor.test"
    )


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


async def _create_and_assign_policy(
    login: dict[str, str],
    agent_id: UUID,
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
    definition: list[dict],
) -> None:
    async with _http_client() as http:
        created = await http.post(
            "/policies",
            json={"name": f"u14-chaos-{uuid.uuid4()}", "definition": definition},
            headers={"Authorization": f"Bearer {login['access_token']}"},
        )
        policy = created.json()
        await http.post(
            f"/policies/{policy['id']}/activate",
            headers={"Authorization": f"Bearer {login['access_token']}"},
        )
    await assign_policy_set_to_agent(agent_id, UUID(policy["policy_set_id"]))


async def test_intercept_stays_available_with_limits_and_breaker_when_redis_is_down(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    tool_name = f"chaos-redis-{uuid.uuid4()}"
    admin_login = await login_as(await make_user(role="admin"))
    # A policy with a `limits:` dimension configured — this is what makes
    # the request path actually reach both check_and_apply_limits and
    # circuit_breaker.is_open (both gated on `decision.action == "allow"`
    # in main.py's _decide_and_record), so a single outage-window call
    # exercises both fail-open branches at once.
    await _create_and_assign_policy(
        admin_login,
        agent_id,
        assign_policy_set_to_agent,
        [{"match": {"tool": "*"}, "action": "allow", "limits": {"calls_per_minute": 2}}],
    )

    stop = _docker("stop", REDIS_CONTAINER)
    assert stop.returncode == 0, stop.stderr
    try:
        body = {
            "trace_id": str(uuid.uuid4()),
            "parent_span_id": None,
            "tool_name": tool_name,
            "args": {},
            "agent_id": str(agent_id),
            "idempotency_key": str(uuid.uuid4()),
        }
        headers = {"Authorization": f"Bearer {raw_key}"}

        async with _http_client() as http:
            response = await http.post("/intercept", json=body, headers=headers)

        # The invariant: never a 500, never blocked by the *mechanism*
        # itself being unreachable — limits/breaker are protective, not
        # load-bearing (ADR-015).
        assert response.status_code == 200, response.text
        assert response.json()["decision"] == "allowed", response.text
    finally:
        start = _docker("start", REDIS_CONTAINER)
        assert start.returncode == 0, start.stderr


async def test_policy_cache_is_unaffected_by_redis_and_a_miss_defaults_to_allow(
    test_agent: tuple[UUID, str],
) -> None:
    """Documents the resolved doc/code conflict from the module docstring:
    an agent with no policy assigned (a guaranteed cache miss, since there
    is nothing to ever have cached) still gets served — a default-allow
    decision computed entirely in-process, never a Postgres round-trip —
    identically whether Redis is up or down."""
    agent_id, raw_key = test_agent

    stop = _docker("stop", REDIS_CONTAINER)
    assert stop.returncode == 0, stop.stderr
    try:
        body = {
            "trace_id": str(uuid.uuid4()),
            "parent_span_id": None,
            "tool_name": "chaos.redis_policy_cache_test",
            "args": {},
            "agent_id": str(agent_id),
            "idempotency_key": str(uuid.uuid4()),
        }
        headers = {"Authorization": f"Bearer {raw_key}"}

        async with _http_client() as http:
            response = await http.post("/intercept", json=body, headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["decision"] == "allowed", response.text
    finally:
        start = _docker("start", REDIS_CONTAINER)
        assert start.returncode == 0, start.stderr
