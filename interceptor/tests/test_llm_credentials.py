"""GET/POST /llm-keys, DELETE /llm-keys/{id} — BYOK LLM provider
credentials (docs/adr/ADR-022). The core claims under test: the plaintext
key is never returned by any response, only `key_last4` is; a stored key
round-trips correctly through encryption when actually used (covered by
test_live_demo.py, which exercises decrypt via a fake provider); and the
personal (not org-shared) scoping matches api_tokens exactly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
from bastion_interceptor.main import app


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://interceptor.test"
    )


def _auth_headers(login: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {login['access_token']}"}


async def test_created_credential_never_returns_the_plaintext_key(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="viewer")
    login = await login_as(user)

    async with _http_client() as http:
        create = await http.post(
            "/llm-keys",
            json={"provider": "openai", "label": "personal key", "api_key": "sk-abcdef123456"},
            headers=_auth_headers(login),
        )
        assert create.status_code == 201
        body = create.json()
        assert "api_key" not in body
        assert "key_ciphertext" not in body
        assert "key_nonce" not in body
        assert body["key_last4"] == "3456"
        assert body["provider"] == "openai"
        assert body["label"] == "personal key"

        listed = await http.get("/llm-keys", headers=_auth_headers(login))
    assert listed.status_code == 200
    assert all("api_key" not in c for c in listed.json())
    assert any(c["id"] == body["id"] for c in listed.json())


async def test_revoked_credential_disappears_from_active_use(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="viewer")
    login = await login_as(user)

    async with _http_client() as http:
        create = await http.post(
            "/llm-keys",
            json={"provider": "anthropic", "label": "temp", "api_key": "sk-ant-000111222"},
            headers=_auth_headers(login),
        )
        credential_id = create.json()["id"]

        revoke = await http.delete(f"/llm-keys/{credential_id}", headers=_auth_headers(login))
        assert revoke.status_code == 204

        second_revoke = await http.delete(
            f"/llm-keys/{credential_id}", headers=_auth_headers(login)
        )
    assert second_revoke.status_code == 404
    assert second_revoke.json()["error"]["code"] == "LLM_CREDENTIAL_NOT_FOUND"


async def test_cannot_revoke_another_users_credential(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    owner = await make_user(role="owner")
    owner_login = await login_as(owner)
    teammate = await make_user(role="viewer", org_id=owner["org_id"])
    teammate_login = await login_as(teammate)

    async with _http_client() as http:
        create = await http.post(
            "/llm-keys",
            json={"provider": "gemini", "label": "owner's key", "api_key": "AIzaSy000000000"},
            headers=_auth_headers(owner_login),
        )
        credential_id = create.json()["id"]

        # Same org, different user — personal credentials aren't an
        # org-shared resource, same as api_tokens.
        revoke = await http.delete(
            f"/llm-keys/{credential_id}", headers=_auth_headers(teammate_login)
        )
        assert revoke.status_code == 404

        listed = await http.get("/llm-keys", headers=_auth_headers(teammate_login))
    assert listed.status_code == 200
    assert all(c["id"] != credential_id for c in listed.json())


async def test_unknown_provider_is_rejected(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="viewer")
    login = await login_as(user)

    async with _http_client() as http:
        create = await http.post(
            "/llm-keys",
            json={"provider": "not-a-real-provider", "label": "x", "api_key": "sk-x"},
            headers=_auth_headers(login),
        )
    assert create.status_code == 422
