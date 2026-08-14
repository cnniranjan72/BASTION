"""GET/POST /users, PATCH /users/{id}/role — team/RBAC management. Mirrors
test_agents.py's structure for the equivalent agent endpoints.
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


async def _other_org() -> uuid.UUID:
    org_id = await db.pool.fetchval(
        "INSERT INTO organizations (id, name) VALUES (gen_random_uuid(), $1) RETURNING id",
        f"other-org-{uuid.uuid4()}",
    )
    assert org_id is not None
    return org_id


async def test_admin_can_provision_teammate_and_password_actually_logs_in(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    admin = await make_user(role="admin")
    login = await login_as(admin)
    email = f"{uuid.uuid4()}@example.com"

    async with _http_client() as http:
        create = await http.post(
            "/users", json={"email": email, "role": "viewer"}, headers=_auth_headers(login)
        )
        assert create.status_code == 201
        body = create.json()
        assert body["role"] == "viewer"
        temp_password = body["temporary_password"]

        listed = await http.get("/users", headers=_auth_headers(login))
        assert listed.status_code == 200
        assert any(u["id"] == body["id"] for u in listed.json())
        assert all("temporary_password" not in u for u in listed.json())

        login_res = await http.post("/auth/login", json={"email": email, "password": temp_password})
    assert login_res.status_code == 200
    assert login_res.json()["role"] == "viewer"


async def test_viewer_cannot_provision_teammate(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="viewer")
    login = await login_as(user)
    async with _http_client() as http:
        response = await http.post(
            "/users",
            json={"email": f"{uuid.uuid4()}@example.com", "role": "viewer"},
            headers=_auth_headers(login),
        )
    assert response.status_code == 403


async def test_users_are_scoped_to_org(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    org_a_admin = await make_user(role="admin")
    org_a_login = await login_as(org_a_admin)
    async with _http_client() as http:
        await http.post(
            "/users",
            json={"email": f"{uuid.uuid4()}@example.com", "role": "viewer"},
            headers=_auth_headers(org_a_login),
        )

    org_b_admin = await make_user(role="admin", org_id=await _other_org())
    org_b_login = await login_as(org_b_admin)
    async with _http_client() as http:
        response = await http.get("/users", headers=_auth_headers(org_b_login))
    assert response.status_code == 200
    emails = [u["email"] for u in response.json()]
    assert org_a_admin["email"] not in emails


async def test_admin_can_change_a_teammates_role(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    admin = await make_user(role="admin")
    login = await login_as(admin)
    teammate = await make_user(role="viewer", org_id=admin["org_id"])

    async with _http_client() as http:
        response = await http.patch(
            f"/users/{teammate['id']}/role",
            json={"role": "approver"},
            headers=_auth_headers(login),
        )
    assert response.status_code == 200
    assert response.json()["role"] == "approver"


async def test_cannot_demote_the_last_owner(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    owner = await make_user(role="owner")
    login = await login_as(owner)
    async with _http_client() as http:
        response = await http.patch(
            f"/users/{owner['id']}/role", json={"role": "admin"}, headers=_auth_headers(login)
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LAST_OWNER"


async def test_can_demote_an_owner_when_another_owner_remains(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    owner_a = await make_user(role="owner")
    login = await login_as(owner_a)
    owner_b = await make_user(role="owner", org_id=owner_a["org_id"])

    async with _http_client() as http:
        response = await http.patch(
            f"/users/{owner_b['id']}/role", json={"role": "admin"}, headers=_auth_headers(login)
        )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
