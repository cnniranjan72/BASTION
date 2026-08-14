"""POST/GET /agents, PATCH /agents/{id} — closes the gap tracked since
Phase 2 (agents only ever creatable via direct SQL). Mirrors
test_policy_engine.py's structure for the equivalent policy endpoints.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import httpx
from bastion_interceptor.db import db
from bastion_interceptor.main import app


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://interceptor.test"
    )


def _auth_headers(login: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {login['access_token']}"}


async def test_admin_can_create_agent_and_key_is_never_returned_again(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="admin")
    login = await login_as(user)
    async with _http_client() as http:
        create = await http.post(
            "/agents", json={"name": "test-agent"}, headers=_auth_headers(login)
        )
        assert create.status_code == 201
        body = create.json()
        assert body["name"] == "test-agent"
        assert body["policy_set_id"] is None
        raw_key = body["api_key"]
        assert raw_key.startswith("bastion_")

        listed = await http.get("/agents", headers=_auth_headers(login))
        assert listed.status_code == 200
        agents = listed.json()
        assert any(a["id"] == body["id"] for a in agents)
        # The list response never carries the key, only the one-time create
        # response does.
        assert all("api_key" not in a for a in agents)

    # The returned key actually authenticates against /intercept.
    async with _http_client() as http:
        intercept = await http.post(
            "/intercept",
            json={
                "trace_id": str(uuid.uuid4()),
                "parent_span_id": None,
                "tool_name": "tool.test",
                "args": {},
                "agent_id": body["id"],
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    assert intercept.status_code == 200
    assert intercept.json()["decision"] == "allowed"


async def test_viewer_cannot_create_agent(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="viewer")
    login = await login_as(user)
    async with _http_client() as http:
        response = await http.post(
            "/agents", json={"name": "test-agent"}, headers=_auth_headers(login)
        )
    assert response.status_code == 403


async def test_viewer_can_list_agents(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    admin = await make_user(role="admin")
    admin_login = await login_as(admin)
    async with _http_client() as http:
        await http.post(
            "/agents", json={"name": "viewer-list-test"}, headers=_auth_headers(admin_login)
        )

    viewer = await make_user(role="viewer", org_id=admin["org_id"])
    viewer_login = await login_as(viewer)
    async with _http_client() as http:
        response = await http.get("/agents", headers=_auth_headers(viewer_login))
    assert response.status_code == 200
    assert any(a["name"] == "viewer-list-test" for a in response.json())


async def _other_org() -> uuid.UUID:
    # make_user()'s own test_org fixture is cached per-test, so two calls
    # with no explicit org_id land in the *same* org — a real second org
    # needs its own row, same pattern as
    # aggregator/tests/test_live_ws.py::test_cross_org_agent_id_closes_connection.
    org_id = await db.pool.fetchval(
        "INSERT INTO organizations (id, name) VALUES (gen_random_uuid(), $1) RETURNING id",
        f"other-org-{uuid.uuid4()}",
    )
    assert org_id is not None
    return org_id


async def test_agents_are_scoped_to_org(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    org_a_admin = await make_user(role="admin")
    org_a_login = await login_as(org_a_admin)
    async with _http_client() as http:
        await http.post("/agents", json={"name": "org-a-only"}, headers=_auth_headers(org_a_login))

    org_b_admin = await make_user(role="admin", org_id=await _other_org())
    org_b_login = await login_as(org_b_admin)
    async with _http_client() as http:
        response = await http.get("/agents", headers=_auth_headers(org_b_login))
    assert response.status_code == 200
    assert all(a["name"] != "org-a-only" for a in response.json())


async def test_create_agent_rejects_policy_set_from_another_org(
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
) -> None:
    other_org_admin = await make_user(role="admin", org_id=await _other_org())
    other_org_login = await login_as(other_org_admin)
    async with _http_client() as http:
        other_policy = await http.post(
            "/policies",
            json={"name": "other-org-policy", "definition": []},
            headers=_auth_headers(other_org_login),
        )
    other_policy_set_id = other_policy.json()["policy_set_id"]

    this_org_admin = await make_user(role="admin")
    this_org_login = await login_as(this_org_admin)
    async with _http_client() as http:
        response = await http.post(
            "/agents",
            json={"name": "cross-org-test", "policy_set_id": other_policy_set_id},
            headers=_auth_headers(this_org_login),
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "POLICY_SET_NOT_FOUND"


async def test_admin_can_reassign_agent_policy_set(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    admin = await make_user(role="admin")
    login = await login_as(admin)
    async with _http_client() as http:
        agent = await http.post(
            "/agents", json={"name": "reassign-test"}, headers=_auth_headers(login)
        )
        agent_id = agent.json()["id"]

        policy = await http.post(
            "/policies",
            json={"name": "reassign-test-policy", "definition": []},
            headers=_auth_headers(login),
        )
        policy_set_id = policy.json()["policy_set_id"]

        updated = await http.patch(
            f"/agents/{agent_id}",
            json={"policy_set_id": policy_set_id},
            headers=_auth_headers(login),
        )
    assert updated.status_code == 200
    assert updated.json()["policy_set_id"] == policy_set_id
