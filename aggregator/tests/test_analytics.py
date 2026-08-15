"""U16 (v2 upgrade) milestone: the 4 new supporting-surface read endpoints
(Threat Center, Agent Health, Cost Center, Command Center) plus Trace
Explorer's new GET /traces filters — API_SPEC.md previously flagged
agent_id/status/tool/policy/time-range as "not implemented yet."

Real infrastructure, not mocks: a real policy is created and activated
through the interceptor's own API, real /intercept + /spans/{id}/complete
calls generate the underlying events, and every assertion below reads back
through the actual aggregator endpoints -- same cross-service pattern as
test_replay.py.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid as uuid_mod
from collections.abc import Awaitable, Callable
from uuid import UUID

import asyncpg
import httpx
import pytest
from bastion_aggregator.main import app as aggregator_app
from bastion_interceptor.main import app as interceptor_app

_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://bastion:bastion@localhost:5442/bastion"
)

BLOCK_LARGE_TRANSFERS = [
    {
        "match": {"tool": "payments.transfer"},
        "condition": "amount > 100",
        "action": "block",
    },
    {"match": {"tool": "*"}, "action": "allow"},
]


def _interceptor_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=interceptor_app), base_url="http://interceptor.test"
    )


def _aggregator_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=aggregator_app), base_url="http://aggregator.test"
    )


def _auth_headers(login: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {login['access_token']}"}


async def _intercept(
    http: httpx.AsyncClient, *, agent_id: UUID, raw_key: str, tool_name: str, args: dict
) -> dict:
    response = await http.post(
        "/intercept",
        json={
            "trace_id": str(uuid_mod.uuid4()),
            "parent_span_id": None,
            "tool_name": tool_name,
            "args": args,
            "agent_id": str(agent_id),
            "idempotency_key": str(uuid_mod.uuid4()),
        },
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _complete(
    http: httpx.AsyncClient,
    *,
    raw_key: str,
    span_id: str,
    status: str,
    cost: float | None,
    latency_ms: float = 10.0,
) -> None:
    response = await http.post(
        f"/spans/{span_id}/complete",
        json={"status": status, "latency_ms": latency_ms, "cost": cost},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert response.status_code == 200, response.text


async def _setup_scenario(
    agent_id: UUID,
    raw_key: str,
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> dict:
    """One agent, one activated policy (blocks payments.transfer > $100),
    5 real calls: 2 allowed+completed payments.transfer (real cost), 1
    blocked payments.transfer, 1 allowed+completed customers.lookup (real
    cost), 1 allowed+failed customers.lookup (no cost, a real failure)."""
    user = await make_user(role="admin")
    login = await login_as(user)

    async with _interceptor_client() as http:
        created = await http.post(
            "/policies",
            json={"name": "u16-analytics-test", "definition": BLOCK_LARGE_TRANSFERS},
            headers=_auth_headers(login),
        )
        policy = created.json()
        await http.post(f"/policies/{policy['id']}/activate", headers=_auth_headers(login))
    await assign_policy_set_to_agent(agent_id, UUID(policy["policy_set_id"]))

    async with _interceptor_client() as http:
        d1 = await _intercept(
            http,
            agent_id=agent_id,
            raw_key=raw_key,
            tool_name="payments.transfer",
            args={"amount": 50},
        )
        assert d1["decision"] == "allowed"
        await _complete(http, raw_key=raw_key, span_id=d1["span_id"], status="completed", cost=0.05)

        d2 = await _intercept(
            http,
            agent_id=agent_id,
            raw_key=raw_key,
            tool_name="payments.transfer",
            args={"amount": 60},
        )
        assert d2["decision"] == "allowed"
        await _complete(http, raw_key=raw_key, span_id=d2["span_id"], status="completed", cost=0.03)

        d3 = await _intercept(
            http,
            agent_id=agent_id,
            raw_key=raw_key,
            tool_name="payments.transfer",
            args={"amount": 200},
        )
        assert d3["decision"] == "blocked"

        d4 = await _intercept(
            http, agent_id=agent_id, raw_key=raw_key, tool_name="customers.lookup", args={"id": 1}
        )
        assert d4["decision"] == "allowed"
        await _complete(http, raw_key=raw_key, span_id=d4["span_id"], status="completed", cost=0.01)

        d5 = await _intercept(
            http, agent_id=agent_id, raw_key=raw_key, tool_name="customers.lookup", args={"id": 2}
        )
        assert d5["decision"] == "allowed"
        await _complete(http, raw_key=raw_key, span_id=d5["span_id"], status="failed", cost=None)

    return {"login": login, "policy": policy, "trace_ids": [d1, d2, d3, d4, d5]}


async def test_threat_center_reflects_real_blocked_calls(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    scenario = await _setup_scenario(
        agent_id, raw_key, make_user, login_as, assign_policy_set_to_agent
    )

    async with _aggregator_client() as http:
        response = await http.get("/threats", headers=_auth_headers(scenario["login"]))
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["blocked_calls_total"] >= 1
    names = {p["policy_name"]: p["block_count"] for p in body["top_violated_policies"]}
    assert names.get("u16-analytics-test", 0) >= 1
    assert isinstance(body["timeline"], list)


async def test_agent_health_reflects_real_call_mix(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    scenario = await _setup_scenario(
        agent_id, raw_key, make_user, login_as, assign_policy_set_to_agent
    )

    async with _aggregator_client() as http:
        response = await http.get(
            f"/agents/{agent_id}/health", headers=_auth_headers(scenario["login"])
        )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["calls_total"] == 5
    assert body["blocked_total"] == 1
    assert body["failed_total"] == 1
    assert 0 <= body["health_score"] <= 100
    tool_names = {t["tool_name"] for t in body["top_tools"]}
    assert tool_names == {"payments.transfer", "customers.lookup"}


async def test_agent_health_cross_org_is_not_found(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
) -> None:
    agent_id, _ = test_agent
    # A user in a *different* org than the agent -- make_user's default org
    # is the same test_org as test_agent, so create a genuinely separate one.
    other_org = uuid_mod.uuid4()
    conn = await asyncpg.connect(_DATABASE_URL)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name) VALUES ($1, $2)", other_org, "other-org"
        )
    finally:
        await conn.close()
    other_login = await login_as(await make_user(role="admin", org_id=other_org))

    async with _aggregator_client() as http:
        response = await http.get(f"/agents/{agent_id}/health", headers=_auth_headers(other_login))
    assert response.status_code == 404


async def test_cost_center_reflects_real_cost_and_estimates_savings(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    scenario = await _setup_scenario(
        agent_id, raw_key, make_user, login_as, assign_policy_set_to_agent
    )

    async with _aggregator_client() as http:
        response = await http.get("/costs", headers=_auth_headers(scenario["login"]))
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_cost"] == pytest.approx(0.09)
    by_tool = {t["tool_name"]: t["cost"] for t in body["by_tool"]}
    assert by_tool["payments.transfer"] == pytest.approx(0.08)
    assert by_tool["customers.lookup"] == pytest.approx(0.01)
    # 1 blocked payments.transfer call, real avg cost for that agent+tool
    # is (0.05+0.03)/2 = 0.04 -- estimated savings must be exactly that.
    assert body["estimated_savings_from_policy_enforcement"] == pytest.approx(0.04)


async def test_command_center_snapshot_reflects_real_state(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    scenario = await _setup_scenario(
        agent_id, raw_key, make_user, login_as, assign_policy_set_to_agent
    )

    async with _aggregator_client() as http:
        response = await http.get("/command-center", headers=_auth_headers(scenario["login"]))
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["agents_total"] == 1
    # No circuit breaker was tripped in this scenario -- real Redis state,
    # not a hardcoded "healthy" flag.
    assert body["agents_healthy"] == 1
    # 1 completed, 1 failed among the pair that report an outcome at all
    # (payments.transfer's 2 completions also count) -> 3 completed, 1 failed.
    assert body["availability_pct"] == pytest.approx(75.0, abs=0.1)
    assert body["last_incident_at"] is not None
    assert len(body["recent_activity"]) >= 1


async def test_traces_filters_reflect_real_data(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    scenario = await _setup_scenario(
        agent_id, raw_key, make_user, login_as, assign_policy_set_to_agent
    )
    login = scenario["login"]

    async def _wait_for_all_persisted(deadline_seconds: float = 5.0) -> None:
        deadline = time.monotonic() + deadline_seconds
        trace_ids = [d["span_id"] for d in scenario["trace_ids"]]
        async with _aggregator_client() as http:
            while time.monotonic() < deadline:
                resp = await http.get("/traces", headers=_auth_headers(login))
                if len(resp.json()) >= len(trace_ids):
                    return
                await asyncio.sleep(0.05)
        raise AssertionError("traces were not all persisted in time")

    await _wait_for_all_persisted()

    async with _aggregator_client() as http:
        by_agent = await http.get(
            "/traces", params={"agent_id": str(agent_id)}, headers=_auth_headers(login)
        )
        assert len(by_agent.json()) == 5

        by_status = await http.get(
            "/traces", params={"status": "had_blocks"}, headers=_auth_headers(login)
        )
        assert len(by_status.json()) >= 1

        by_tool = await http.get(
            "/traces", params={"tool": "customers.lookup"}, headers=_auth_headers(login)
        )
        assert len(by_tool.json()) == 2

        by_policy = await http.get(
            "/traces", params={"policy": "u16-analytics-test"}, headers=_auth_headers(login)
        )
        assert len(by_policy.json()) >= 1

        by_future_window = await http.get(
            "/traces",
            params={"started_after": "2099-01-01T00:00:00Z"},
            headers=_auth_headers(login),
        )
        assert by_future_window.json() == []
