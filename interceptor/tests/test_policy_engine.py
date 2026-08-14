"""Phase 2 milestone (BUILD_PLAN.md): change a policy via the API and see
the running interceptor's behavior change with no restart. Also covers the
safe condition evaluator, versioning semantics, and the multi-tenancy
isolation CLAUDE.md rule #7 requires.
"""

from __future__ import annotations

import asyncio
import time
import uuid
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


def _auth_headers(login: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {login['access_token']}"}


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


async def test_create_policy_does_not_mutate_previous_version(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="admin")
    login = await login_as(user)
    async with _http_client() as http:
        v1 = await http.post(
            "/policies",
            json={"name": "versioning-test", "definition": []},
            headers=_auth_headers(login),
        )
        v2 = await http.post(
            "/policies",
            json={"name": "versioning-test", "definition": []},
            headers=_auth_headers(login),
        )
    assert v1.json()["version"] == 1
    assert v2.json()["version"] == 2
    assert v1.json()["id"] != v2.json()["id"]
    assert v1.json()["policy_set_id"] == v2.json()["policy_set_id"]


async def test_create_policy_omitting_based_on_version_keeps_v1_blind_append_behavior(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    """U4 (v2 upgrade): based_on_version is additive, not a breaking change —
    a caller that never sends it (the whole SDK/test surface predating U4)
    gets the exact same behavior as before this phase, no 409 possible."""
    user = await make_user(role="admin")
    login = await login_as(user)
    async with _http_client() as http:
        v1 = await http.post(
            "/policies",
            json={"name": "no-based-on-version-test", "definition": []},
            headers=_auth_headers(login),
        )
        v2 = await http.post(
            "/policies",
            json={"name": "no-based-on-version-test", "definition": []},
            headers=_auth_headers(login),
        )
    assert v1.status_code == 201
    assert v2.status_code == 201
    assert v2.json()["version"] == 2


async def test_create_policy_with_correct_based_on_version_succeeds(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="admin")
    login = await login_as(user)
    async with _http_client() as http:
        v1 = await http.post(
            "/policies",
            json={"name": "correct-based-on-version-test", "definition": []},
            headers=_auth_headers(login),
        )
        assert v1.json()["version"] == 1
        v2 = await http.post(
            "/policies",
            json={
                "name": "correct-based-on-version-test",
                "definition": [],
                "based_on_version": 1,
            },
            headers=_auth_headers(login),
        )
    assert v2.status_code == 201
    assert v2.json()["version"] == 2


async def test_concurrent_policy_updates_from_stale_version_one_wins_one_gets_409(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    """U4 milestone test (UPGRADE_BUILD_PLAN.md): two concurrent updates to
    the same policy from stale versions — assert exactly one succeeds and
    the other gets a clean 409, not a silent overwrite. See ADR-016 for why
    "update" means "create the next version" here, not an in-place edit —
    v1's policies table is deliberately append-only
    (test_create_policy_does_not_mutate_previous_version, above)."""
    user = await make_user(role="admin")
    login = await login_as(user)
    name = "concurrent-conflict-test"
    async with _http_client() as http:
        v1 = await http.post(
            "/policies", json={"name": name, "definition": []}, headers=_auth_headers(login)
        )
        assert v1.json()["version"] == 1

        # Two admins who both last saw version 1 as current, editing at the
        # same time — both attempt to create version 2 based on that same
        # (soon to be stale, for one of them) starting point.
        results = await asyncio.gather(
            http.post(
                "/policies",
                json={"name": name, "definition": [], "based_on_version": 1},
                headers=_auth_headers(login),
            ),
            http.post(
                "/policies",
                json={"name": name, "definition": [], "based_on_version": 1},
                headers=_auth_headers(login),
            ),
        )

    statuses = sorted(r.status_code for r in results)
    assert statuses == [201, 409], (
        f"expected exactly one 201 and one 409, got {[r.status_code for r in results]}"
    )

    conflict_response = next(r for r in results if r.status_code == 409)
    conflict_body = conflict_response.json()
    assert conflict_body["error"]["code"] == "POLICY_VERSION_CONFLICT"
    assert "request_id" in conflict_body["error"]

    # Not a silent overwrite: exactly one new version (2) exists, the loser
    # created nothing.
    async with _http_client() as http:
        listing = await http.get("/policies", headers=_auth_headers(login))
    versions = sorted(p["version"] for p in listing.json() if p["name"] == name)
    assert versions == [1, 2]


async def test_create_policy_rejects_unsafe_condition_with_error_envelope(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="admin")
    login = await login_as(user)
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
            json={"name": "unsafe-condition", "definition": definition},
            headers=_auth_headers(login),
        )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_POLICY_CONDITION"
    assert "request_id" in body["error"]


async def test_policy_governs_intercept_decisions(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    user = await make_user(role="admin")
    login = await login_as(user)
    async with _http_client() as http:
        created = await http.post(
            "/policies",
            json={"name": "intercept-integration", "definition": DELETE_ON_PRODUCTION_BLOCKED},
            headers=_auth_headers(login),
        )
        policy = created.json()
        await http.post(f"/policies/{policy['id']}/activate", headers=_auth_headers(login))

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


async def test_hot_reload_propagates_via_pubsub_within_a_few_seconds(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    """Simulates a *second* interceptor instance: its own PolicyCache and
    its own Redis subscription, independent of the module-level cache the
    app under test uses. Proves the reload travels over the wire (real
    Redis, real pub/sub), not just "the same process updated its own dict."
    """
    user = await make_user(role="admin")
    login = await login_as(user)

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
                json={"name": "hot-reload-test", "definition": DELETE_ON_PRODUCTION_BLOCKED},
                headers=_auth_headers(login),
            )
            policy = created.json()
            await http.post(f"/policies/{policy['id']}/activate", headers=_auth_headers(login))

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


async def test_org_cannot_read_another_orgs_policies(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user_a = await make_user(role="admin")
    login_a = await login_as(user_a)

    org_b = uuid.uuid4()
    conn = await db.pool.acquire()
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, 'org-b')", org_b)
    finally:
        await db.pool.release(conn)
    user_b = await make_user(role="admin", org_id=org_b)
    login_b = await login_as(user_b)

    async with _http_client() as http:
        await http.post(
            "/policies",
            json={"name": "org-a-secret", "definition": []},
            headers=_auth_headers(login_a),
        )
        response = await http.get("/policies", headers=_auth_headers(login_b))

    assert response.json() == []
