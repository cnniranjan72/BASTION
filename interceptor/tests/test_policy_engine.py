"""Phase 2 milestone (BUILD_PLAN.md): change a policy via the API and see
the running interceptor's behavior change with no restart. Also covers the
safe condition evaluator, versioning semantics, and the multi-tenancy
isolation CLAUDE.md rule #7 requires.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
import pytest
from bastion import BastionBlockedError, BastionClient
from bastion_interceptor import policy as policy_engine
from bastion_interceptor.db import db
from bastion_interceptor.main import app
from bastion_interceptor.redis_bus import RedisBus

DELETE_ON_PRODUCTION_BLOCKED = [
    {
        "match": {"tool": "db.query", "pattern": "^DELETE", "database": "production"},
        "action": "block",
    },
    {"match": {"tool": "*"}, "action": "allow"},
]


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://interceptor.test"
    )


def _bastion_client(agent_id: UUID, raw_key: str) -> BastionClient:
    return BastionClient(
        base_url="http://interceptor.test",
        api_key=raw_key,
        agent_id=agent_id,
        transport=httpx.ASGITransport(app=app),
    )


async def _noop() -> str:
    return "ok"


# -- Safe condition evaluator (no DB/HTTP) -------------------------------


def test_condition_evaluator_supports_safe_comparisons() -> None:
    compiled = policy_engine.compile_condition("amount > 50")
    assert policy_engine.evaluate_condition(compiled, {"amount": 75}) is True
    assert policy_engine.evaluate_condition(compiled, {"amount": 10}) is False


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('echo pwned')",
        "open('/etc/passwd').read()",
        "(lambda: 1)()",
        "[x for x in range(10)]",
        "amount.__class__",
    ],
)
def test_condition_evaluator_rejects_unsafe_expressions(expr: str) -> None:
    with pytest.raises(policy_engine.PolicyConditionError):
        policy_engine.compile_condition(expr)


# -- Policy CRUD + versioning + intercept integration --------------------


async def test_create_policy_does_not_mutate_previous_version(test_org: UUID) -> None:
    async with _http_client() as http:
        v1 = await http.post(
            "/policies",
            json={"org_id": str(test_org), "name": "versioning-test", "definition": []},
        )
        v2 = await http.post(
            "/policies",
            json={"org_id": str(test_org), "name": "versioning-test", "definition": []},
        )
    assert v1.json()["version"] == 1
    assert v2.json()["version"] == 2
    assert v1.json()["id"] != v2.json()["id"]
    assert v1.json()["policy_set_id"] == v2.json()["policy_set_id"]


async def test_create_policy_rejects_unsafe_condition_with_error_envelope(test_org: UUID) -> None:
    definition = [
        {
            "match": {"tool": "payments.charge"},
            "action": "require_approval",
            "condition": "__import__('os').system('rm -rf /')",
        }
    ]
    async with _http_client() as http:
        response = await http.post(
            "/policies",
            json={"org_id": str(test_org), "name": "unsafe-condition", "definition": definition},
        )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_POLICY_CONDITION"
    assert "request_id" in body["error"]


async def test_policy_governs_intercept_decisions(
    test_org: UUID,
    test_agent: tuple[UUID, str],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    async with _http_client() as http:
        created = await http.post(
            "/policies",
            json={
                "org_id": str(test_org),
                "name": "intercept-integration",
                "definition": DELETE_ON_PRODUCTION_BLOCKED,
            },
        )
        policy = created.json()
        await http.post(f"/policies/{policy['id']}/activate")

    # /policies/{id}/activate already updated this process's policy_cache
    # synchronously (before publishing to Redis) — no reload needed here.
    await assign_policy_set_to_agent(agent_id, UUID(policy["policy_set_id"]))

    async with _bastion_client(agent_id, raw_key) as client:
        allowed = await client.call(
            "db.query", {"query": "SELECT 1", "database": "production"}, execute=_noop
        )
        assert allowed == "ok"

        with pytest.raises(BastionBlockedError):
            await client.call(
                "db.query",
                {"query": "DELETE FROM users", "database": "production"},
                execute=_noop,
            )


# -- Hot reload via Redis pub/sub: the Phase 2 milestone -----------------


async def test_hot_reload_propagates_via_pubsub_within_a_few_seconds(test_org: UUID) -> None:
    """Simulates a *second* interceptor instance: its own PolicyCache and
    its own Redis subscription, independent of the module-level cache the
    app under test uses. Proves the reload travels over the wire (real
    Redis, real pub/sub), not just "the same process updated its own dict."
    """
    other_cache = policy_engine.PolicyCache()
    other_bus = RedisBus()
    await other_bus.connect()
    received: list[UUID] = []

    async def on_update(policy_set_id: UUID) -> None:
        record = await db.get_active_policy_for_set(policy_set_id)
        if record is not None:
            compiled = policy_engine.compile_policy_from_raw(
                record["id"], record["policy_set_id"], record["definition"]
            )
            other_cache.put(compiled)
        received.append(policy_set_id)

    await other_bus.start_policy_listener(on_update)

    try:
        async with _http_client() as http:
            created = await http.post(
                "/policies",
                json={
                    "org_id": str(test_org),
                    "name": "hot-reload-test",
                    "definition": DELETE_ON_PRODUCTION_BLOCKED,
                },
            )
            policy = created.json()
            await http.post(f"/policies/{policy['id']}/activate")

        policy_set_id = UUID(policy["policy_set_id"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and policy_set_id not in received:
            await asyncio.sleep(0.05)

        assert policy_set_id in received
        cached = other_cache.get(policy_set_id)
        assert cached is not None
        assert len(cached.rules) == len(DELETE_ON_PRODUCTION_BLOCKED)
    finally:
        await other_bus.close()


# -- Multi-tenancy isolation (CLAUDE.md rule #7) --------------------------


async def test_org_cannot_read_another_orgs_policies(test_org: UUID) -> None:
    org_a = test_org
    async with _http_client() as http:
        await http.post(
            "/policies",
            json={"org_id": str(org_a), "name": "org-a-secret", "definition": []},
        )

        org_b = await db.pool.fetchval(
            "INSERT INTO organizations (id, name) VALUES (gen_random_uuid(), 'org-b') RETURNING id"
        )
        response = await http.get("/policies", params={"org_id": str(org_b)})

    assert response.json() == []
