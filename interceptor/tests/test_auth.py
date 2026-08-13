"""Phase 5 milestone (BUILD_PLAN.md): simulate refresh token theft (reuse an
already-rotated token) and assert the whole family gets revoked. Also covers
login, RBAC, and logout — the other pieces of AUTH.md §2.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
from bastion_interceptor.main import app


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://interceptor.test"
    )


async def test_login_succeeds_with_correct_credentials(
    make_user: Callable[..., Awaitable[dict]],
) -> None:
    user = await make_user(role="owner")
    async with _http_client() as http:
        response = await http.post(
            "/auth/login", json={"email": user["email"], "password": user["password"]}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "owner"
    assert body["access_token"]
    assert body["refresh_token"]


async def test_login_fails_with_wrong_password(make_user: Callable[..., Awaitable[dict]]) -> None:
    user = await make_user(role="owner")
    async with _http_client() as http:
        response = await http.post(
            "/auth/login", json={"email": user["email"], "password": "wrong password"}
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


# -- Refresh rotation + reuse detection: the Phase 5 milestone ------------


async def test_refresh_rotates_and_old_token_is_one_time_use(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="owner")
    first = await login_as(user)

    async with _http_client() as http:
        second = await http.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["refresh_token"] != first["refresh_token"]

    # The rotated-away token must not work a second time.
    async with _http_client() as http:
        reuse = await http.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert reuse.status_code == 401


async def test_refresh_token_reuse_revokes_entire_family(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    """The actual scenario AUTH.md describes: a stolen refresh token gets
    used by an attacker, rotating it. The legitimate client, still holding
    the *original* (now-consumed) token, later tries to use it too — that
    reuse is the signal. The whole family must die, including the token the
    attacker just legitimately obtained, not just reject the stale one.
    """
    user = await make_user(role="owner")
    login = await login_as(user)

    async with _http_client() as http:
        # Attacker (or the legitimate client racing itself) rotates first.
        attacker_rotation = await http.post(
            "/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )
    assert attacker_rotation.status_code == 200
    attacker_token = attacker_rotation.json()["refresh_token"]

    async with _http_client() as http:
        # The original token gets presented again — reuse detected.
        reuse_attempt = await http.post(
            "/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )
    assert reuse_attempt.status_code == 401
    assert reuse_attempt.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"

    # The token issued by the *legitimate-looking* rotation above must now
    # also be dead — reuse revokes the whole family, not just the replay.
    async with _http_client() as http:
        post_theft_attempt = await http.post(
            "/auth/refresh", json={"refresh_token": attacker_token}
        )
    assert post_theft_attempt.status_code == 401


async def test_logout_revokes_family(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="owner")
    login = await login_as(user)

    async with _http_client() as http:
        logout_response = await http.post(
            "/auth/logout", json={"refresh_token": login["refresh_token"]}
        )
    assert logout_response.status_code == 200

    async with _http_client() as http:
        post_logout_refresh = await http.post(
            "/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )
    assert post_logout_refresh.status_code == 401


# -- RBAC ------------------------------------------------------------------


async def test_viewer_cannot_create_policy(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="viewer")
    login = await login_as(user)

    async with _http_client() as http:
        response = await http.post(
            "/policies",
            json={"name": "viewer-attempt", "definition": []},
            headers={"Authorization": f"Bearer {login['access_token']}"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_admin_can_create_policy(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="admin")
    login = await login_as(user)

    async with _http_client() as http:
        response = await http.post(
            "/policies",
            json={"name": "admin-created", "definition": []},
            headers={"Authorization": f"Bearer {login['access_token']}"},
        )
    assert response.status_code == 201
    assert response.json()["org_id"] == str(user["org_id"])


async def test_missing_token_rejected() -> None:
    async with _http_client() as http:
        response = await http.get("/policies")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "MISSING_ACCESS_TOKEN"


@pytest.mark.parametrize("bad_token", ["not-a-jwt", ""])
async def test_malformed_token_rejected(bad_token: str) -> None:
    async with _http_client() as http:
        response = await http.get("/policies", headers={"Authorization": f"Bearer {bad_token}"})
    assert response.status_code == 401
