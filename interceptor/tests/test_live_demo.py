"""POST /demo/live-run (docs/adr/ADR-022): a real LLM decides which tool to
call, but every decision still goes through the actual policy engine — the
core claim under test is that path, not any particular provider's API.
`call_llm_with_tools` is monkeypatched to a scripted fake so these tests are
deterministic and hit no network, mirroring how `bastion_interceptor.main`
holds its own imported reference to it (patching that reference, not
`bastion_shared.llm`'s, is what actually takes effect here).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import bastion_interceptor.main as main_module
import httpx
import pytest
from bastion_interceptor.main import app
from bastion_shared import LLMAuthError, LLMDecision, LLMRateLimitedError, ToolCallDecision


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://interceptor.test"
    )


def _auth_headers(login: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {login['access_token']}"}


def _scripted_llm(decisions: list[LLMDecision]) -> Callable[..., Awaitable[LLMDecision]]:
    calls = iter(decisions)

    async def fake_call_llm_with_tools(**_kwargs: Any) -> LLMDecision:
        return next(calls)

    return fake_call_llm_with_tools


async def test_llm_deciding_to_make_the_injected_transfer_is_actually_blocked(
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scenario's real point: even though a fake "LLM" decides to make
    the >$100 transfer (standing in for a model that fell for the
    injection), the real policy engine — not the LLM, not this test —
    is what blocks it."""
    user = await make_user(role="viewer")
    login = await login_as(user)

    monkeypatch.setattr(
        main_module,
        "call_llm_with_tools",
        _scripted_llm(
            [
                LLMDecision(
                    tool_call=ToolCallDecision(tool_name="tickets.read", arguments={}),
                    final_text=None,
                ),
                LLMDecision(
                    tool_call=ToolCallDecision(
                        tool_name="payments.transfer",
                        arguments={"to": "attacker-9999", "amount": 500, "memo": "resolve ticket"},
                    ),
                    final_text=None,
                ),
                LLMDecision(tool_call=None, final_text="I was unable to complete this request."),
            ]
        ),
    )

    async with _http_client() as http:
        response = await http.post(
            "/demo/live-run", json={"provider": "ollama"}, headers=_auth_headers(login)
        )
    assert response.status_code == 200
    body = response.json()
    steps = body["steps"]
    assert steps[0]["tool_name"] == "tickets.read"
    assert steps[0]["decision"] == "allowed"
    assert steps[1]["tool_name"] == "payments.transfer"
    assert steps[1]["decision"] == "blocked"
    assert steps[1]["reason"] is not None
    assert body["final_text"] == "I was unable to complete this request."


async def test_legit_transfer_under_the_threshold_is_allowed(
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await make_user(role="viewer")
    login = await login_as(user)

    monkeypatch.setattr(
        main_module,
        "call_llm_with_tools",
        _scripted_llm(
            [
                LLMDecision(
                    tool_call=ToolCallDecision(
                        tool_name="payments.transfer",
                        arguments={"to": "customer-4471", "amount": 25.0, "memo": "refund"},
                    ),
                    final_text=None,
                ),
                LLMDecision(tool_call=None, final_text="Refund sent."),
            ]
        ),
    )

    async with _http_client() as http:
        response = await http.post(
            "/demo/live-run", json={"provider": "ollama"}, headers=_auth_headers(login)
        )
    assert response.status_code == 200
    steps = response.json()["steps"]
    assert steps[0]["decision"] == "allowed"
    assert steps[0]["result"]["status"] == "sent"


async def test_ollama_rejects_a_credential_id(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="viewer")
    login = await login_as(user)

    async with _http_client() as http:
        response = await http.post(
            "/demo/live-run",
            json={"provider": "ollama", "credential_id": "11111111-1111-1111-1111-111111111111"},
            headers=_auth_headers(login),
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OLLAMA_TAKES_NO_CREDENTIAL"


async def test_cloud_provider_without_credential_id_is_rejected(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="viewer")
    login = await login_as(user)

    async with _http_client() as http:
        response = await http.post(
            "/demo/live-run", json={"provider": "openai"}, headers=_auth_headers(login)
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CREDENTIAL_REQUIRED"


async def test_credential_belonging_to_another_user_is_not_usable(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    owner = await make_user(role="owner")
    owner_login = await login_as(owner)
    teammate = await make_user(role="viewer", org_id=owner["org_id"])
    teammate_login = await login_as(teammate)

    async with _http_client() as http:
        create = await http.post(
            "/llm-keys",
            json={"provider": "openai", "label": "owner's key", "api_key": "sk-abcdef123456"},
            headers=_auth_headers(owner_login),
        )
        credential_id = create.json()["id"]

        response = await http.post(
            "/demo/live-run",
            json={"provider": "openai", "credential_id": credential_id},
            headers=_auth_headers(teammate_login),
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "LLM_CREDENTIAL_NOT_FOUND"


async def test_invalid_key_surfaces_as_a_structured_422_not_a_500(
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await make_user(role="viewer")
    login = await login_as(user)

    async def fake_raises_auth_error(**_kwargs: Any) -> LLMDecision:
        raise LLMAuthError("openai", 401, "invalid api key")

    monkeypatch.setattr(main_module, "call_llm_with_tools", fake_raises_auth_error)

    async with _http_client() as http:
        create = await http.post(
            "/llm-keys",
            json={"provider": "openai", "label": "bad key", "api_key": "sk-invalid"},
            headers=_auth_headers(login),
        )
        credential_id = create.json()["id"]

        response = await http.post(
            "/demo/live-run",
            json={"provider": "openai", "credential_id": credential_id},
            headers=_auth_headers(login),
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "LLM_KEY_INVALID"


async def test_rate_limited_key_surfaces_as_a_structured_429(
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The concrete answer to "handle user-side API key limits properly"
    (ADR-022): BASTION cannot raise the user's own provider-side limit, but
    it must fail legibly, not opaquely, when that limit is hit."""
    user = await make_user(role="viewer")
    login = await login_as(user)

    async def fake_raises_rate_limit(**_kwargs: Any) -> LLMDecision:
        raise LLMRateLimitedError("openai", 429, "rate limit exceeded")

    monkeypatch.setattr(main_module, "call_llm_with_tools", fake_raises_rate_limit)

    async with _http_client() as http:
        create = await http.post(
            "/llm-keys",
            json={"provider": "openai", "label": "rate limited key", "api_key": "sk-abcdef123456"},
            headers=_auth_headers(login),
        )
        credential_id = create.json()["id"]

        response = await http.post(
            "/demo/live-run",
            json={"provider": "openai", "credential_id": credential_id},
            headers=_auth_headers(login),
        )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "LLM_RATE_LIMITED"
