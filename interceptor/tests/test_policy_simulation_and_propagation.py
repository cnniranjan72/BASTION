"""U15 (v2 upgrade) milestone: Policy Studio's simulator and
propagation-status panel need real backend endpoints, not UI-only
approximations (FRONTEND_V2.md). Covers:

1. POST /policies/simulate reuses the real policy_cache + evaluate() chain
   (block/allow/require_approval), never a separate simulated evaluator.
2. Configured `limits` are surfaced informationally without ever being
   applied against real Redis state — simulating past a calls_per_minute
   limit N+1 times must never actually block or consume the agent's real
   budget.
3. GET /policies/{policy_set_id}/propagation compares Postgres's real
   active version against this instance's real live policy_cache.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from bastion_interceptor.main import app


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://interceptor.test"
    )


def _auth_headers(login: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {login['access_token']}"}


async def _create_and_assign_policy(
    login: dict[str, str],
    agent_id: UUID,
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
    definition: list[dict],
) -> dict:
    async with _http_client() as http:
        created = await http.post(
            "/policies",
            json={"name": f"u15-sim-{uuid.uuid4()}", "definition": definition},
            headers=_auth_headers(login),
        )
        policy = created.json()
        await http.post(f"/policies/{policy['id']}/activate", headers=_auth_headers(login))
    await assign_policy_set_to_agent(agent_id, UUID(policy["policy_set_id"]))
    return policy


async def test_simulate_walks_the_real_evaluation_chain(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, _ = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    policy = await _create_and_assign_policy(
        admin_login,
        agent_id,
        assign_policy_set_to_agent,
        [
            {"match": {"tool": "payments.transfer"}, "action": "block"},
            {"match": {"tool": "*"}, "action": "allow"},
        ],
    )

    async with _http_client() as http:
        blocked = await http.post(
            "/policies/simulate",
            json={"agent_id": str(agent_id), "tool_name": "payments.transfer", "args": {}},
            headers=_auth_headers(admin_login),
        )
        allowed = await http.post(
            "/policies/simulate",
            json={"agent_id": str(agent_id), "tool_name": "customers.lookup", "args": {}},
            headers=_auth_headers(admin_login),
        )

    assert blocked.status_code == 200, blocked.text
    blocked_body = blocked.json()
    assert blocked_body["decision"] == "block"
    assert "payments.transfer" in (blocked_body["reason"] or "")
    assert blocked_body["policy_id"] == policy["id"]

    assert allowed.status_code == 200, allowed.text
    allowed_body = allowed.json()
    assert allowed_body["decision"] == "allow"
    assert allowed_body["policy_id"] == policy["id"]


async def test_simulate_reports_limits_without_applying_them(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, _ = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    await _create_and_assign_policy(
        admin_login,
        agent_id,
        assign_policy_set_to_agent,
        [{"match": {"tool": "*"}, "action": "allow", "limits": {"calls_per_minute": 1}}],
    )

    async with _http_client() as http:
        # More simulated calls than the configured limit would ever allow
        # for real — none of them may actually be blocked, because a
        # simulation must never consume real rate-limit budget.
        responses = [
            await http.post(
                "/policies/simulate",
                json={
                    "agent_id": str(agent_id),
                    "tool_name": "chaos.sim_limits_test",
                    "args": {},
                },
                headers=_auth_headers(admin_login),
            )
            for _ in range(5)
        ]

    for response in responses:
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["decision"] == "allow"
        assert body["configured_limits"]["calls_per_minute"] == 1


async def test_propagation_reflects_real_postgres_and_cache_state(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, _ = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    policy = await _create_and_assign_policy(
        admin_login,
        agent_id,
        assign_policy_set_to_agent,
        [{"match": {"tool": "*"}, "action": "allow"}],
    )
    policy_set_id = policy["policy_set_id"]

    async with _http_client() as http:
        response = await http.get(
            f"/policies/{policy_set_id}/propagation", headers=_auth_headers(admin_login)
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active_version"] == policy["version"]
    assert body["active_policy_id"] == policy["id"]
    assert body["this_instance_cached_version"] == policy["version"]
    assert body["propagated"] is True
    assert body["known_interceptor_instances"] == 1
