"""GET/POST /api-tokens, DELETE /api-tokens/{id} — personal API tokens for
programmatic access to the management API (post-launch, "add an access
token system for outside users"). The core claim under test: a created
token actually authenticates a request the same way a JWT session would,
not just that the create/list/revoke calls succeed in isolation.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import httpx
from bastion_interceptor.main import app


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://interceptor.test"
    )


def _auth_headers(login: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {login['access_token']}"}


async def test_created_token_actually_authenticates_and_is_shown_once(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="viewer")
    login = await login_as(user)

    async with _http_client() as http:
        create = await http.post(
            "/api-tokens", json={"name": "CI pipeline"}, headers=_auth_headers(login)
        )
        assert create.status_code == 201
        body = create.json()
        assert body["token"].startswith("bstn_pat_")
        raw_token = body["token"]

        listed = await http.get("/api-tokens", headers=_auth_headers(login))
        assert listed.status_code == 200
        assert all("token" not in t for t in listed.json())
        assert any(t["id"] == body["id"] for t in listed.json())

        # The actual point of the feature: this token authenticates a real
        # management-API call, with the same RBAC the user's own JWT gets.
        response = await http.get("/agents", headers={"Authorization": f"Bearer {raw_token}"})
    assert response.status_code == 200


async def test_revoked_token_stops_working(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="viewer")
    login = await login_as(user)

    async with _http_client() as http:
        create = await http.post(
            "/api-tokens", json={"name": "temp"}, headers=_auth_headers(login)
        )
        token_id = create.json()["id"]
        raw_token = create.json()["token"]

        revoke = await http.delete(f"/api-tokens/{token_id}", headers=_auth_headers(login))
        assert revoke.status_code == 204

        response = await http.get("/agents", headers={"Authorization": f"Bearer {raw_token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_ACCESS_TOKEN"


async def test_cannot_revoke_another_users_token(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    owner = await make_user(role="owner")
    owner_login = await login_as(owner)
    teammate = await make_user(role="viewer", org_id=owner["org_id"])
    teammate_login = await login_as(teammate)

    async with _http_client() as http:
        create = await http.post(
            "/api-tokens", json={"name": "owner's token"}, headers=_auth_headers(owner_login)
        )
        token_id = create.json()["id"]

        # Same org, different user — personal tokens aren't an org-shared
        # resource the way agents/policies are.
        revoke = await http.delete(
            f"/api-tokens/{token_id}", headers=_auth_headers(teammate_login)
        )
    assert revoke.status_code == 404


async def test_garbage_bearer_token_with_pat_prefix_is_rejected() -> None:
    async with _http_client() as http:
        response = await http.get(
            "/agents", headers={"Authorization": f"Bearer bstn_pat_{uuid.uuid4().hex}"}
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_ACCESS_TOKEN"
