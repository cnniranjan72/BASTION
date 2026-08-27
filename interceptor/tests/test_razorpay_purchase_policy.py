"""Track 01: the two new razorpay.purchase policy rules
(demo-agent/demo_agent/seed.py's POLICY_DEFINITION), tested at the
interceptor level directly — same reused patterns as
test_approval_flow.py (require_approval) and
test_circuit_breaker_and_limits.py (calls_per_minute), no new policy
mechanism, no engine changes.

The oversized-purchase -> require_approval path is verified here, not
run live in the demo script (demo_agent/run_purchase_demo.py) — resolving
a live pending approval needs either a real human or the SDK's own
fail-closed timeout (25-60s with nobody there to approve it), which isn't
representative of "an agent gone wrong" and is a bad look on camera. The
live demo instead exercises the rate-limit rule, which produces an
immediate `blocked` decision — see run_purchase_demo.py's own docstring.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
import pytest
from bastion import BastionBlockedError, BastionClient
from bastion_interceptor.main import app

RAZORPAY_PURCHASE_POLICY = [
    {
        "match": {"tool": "razorpay.purchase"},
        "action": "require_approval",
        "condition": "amount_inr > 18000",
    },
    {
        "match": {"tool": "razorpay.purchase"},
        "action": "allow",
        "limits": {"calls_per_minute": 3},
    },
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


async def _create_and_assign_policy(
    login: dict[str, str],
    agent_id: UUID,
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
    name: str,
) -> None:
    async with _http_client() as http:
        created = await http.post(
            "/policies",
            json={"name": name, "definition": RAZORPAY_PURCHASE_POLICY},
            headers=_auth_headers(login),
        )
        policy = created.json()
        await http.post(f"/policies/{policy['id']}/activate", headers=_auth_headers(login))
    await assign_policy_set_to_agent(agent_id, UUID(policy["policy_set_id"]))


async def test_purchase_under_threshold_is_allowed(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    await _create_and_assign_policy(
        admin_login, agent_id, assign_policy_set_to_agent, "razorpay-purchase-under-threshold"
    )

    async def execute() -> dict[str, object]:
        return {"status": "captured"}

    async with _bastion_client(agent_id, raw_key) as client:
        result = await client.call(
            "razorpay.purchase",
            {"sku": "EARBUDS-PRO", "quantity": 1, "amount_inr": 1499},
            execute,
        )
    assert result == {"status": "captured"}


async def test_purchase_over_threshold_requires_approval(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    """Fast, direct assertion on the raw /intercept decision — not routed
    through BastionClient.call()'s long-poll wait, which would need a real
    human or a real ~25-60s timeout to resolve either way."""
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    await _create_and_assign_policy(
        admin_login, agent_id, assign_policy_set_to_agent, "razorpay-purchase-over-threshold"
    )

    async with _http_client() as http:
        response = await http.post(
            "/intercept",
            json={
                "trace_id": "11111111-2222-3333-4444-555555555555",
                "parent_span_id": None,
                "tool_name": "razorpay.purchase",
                "args": {"sku": "SSD-1TB", "quantity": 4, "amount_inr": 23996},
                "agent_id": str(agent_id),
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    body = response.json()
    assert body["decision"] == "pending_approval"
    assert "approval_request_id" in body


async def test_purchase_rate_limit_blocks_once_exceeded(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    await _create_and_assign_policy(
        admin_login, agent_id, assign_policy_set_to_agent, "razorpay-purchase-rate-limit"
    )

    async def execute() -> dict[str, object]:
        return {"status": "captured"}

    async with _bastion_client(agent_id, raw_key) as client:
        for _ in range(3):
            result = await client.call(
                "razorpay.purchase",
                {"sku": "CHARGER-65W", "quantity": 1, "amount_inr": 899},
                execute,
            )
            assert result == {"status": "captured"}

        with pytest.raises(BastionBlockedError) as exc_info:
            await client.call(
                "razorpay.purchase",
                {"sku": "CHARGER-65W", "quantity": 1, "amount_inr": 899},
                execute,
            )
    assert "calls_per_minute" in (exc_info.value.reason or "")
