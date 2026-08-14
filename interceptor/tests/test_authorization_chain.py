"""U7 milestone test (UPGRADE_BUILD_PLAN.md): "can approver X approve this
$250 payment for agent Y" is a fully traceable Subject -> Role -> Resource ->
Action -> Policy evaluation (UPGRADE_ARCHITECTURE.md §9), reusing the exact
same evaluator (policy.py's `evaluate`) a tool-call decision goes through —
proven here by configuring an authorization policy purely through the
existing POST /policies + POST /policies/{id}/activate endpoints (no new
API surface at all), and observing a denial come back with a "Why?" reason
in the same error-envelope shape a tool-call block already uses.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from bastion import BastionClient
from bastion_interceptor.authorization import AUTHORIZATION_POLICY_SET_NAME
from bastion_interceptor.main import app

REQUIRE_APPROVAL_FOR_PAYMENTS = [
    {"match": {"tool": "payments.charge"}, "action": "require_approval"},
]

# The authorization DSL reuses PolicyRule verbatim: `match.tool` here means
# "action" (approve/deny), and `condition`/`args` here mean "resource" —
# the same evaluator, a different rule set, exactly as UPGRADE_ARCHITECTURE.md
# §9 asks for.
BLOCK_APPROVALS_OVER_200 = [
    {"match": {"tool": "approve"}, "action": "block", "condition": "amount > 200"},
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


async def _create_and_activate(login: dict[str, str], name: str, definition: list[dict]) -> dict:
    async with _http_client() as http:
        created = await http.post(
            "/policies", json={"name": name, "definition": definition}, headers=_auth_headers(login)
        )
        policy = created.json()
        await http.post(f"/policies/{policy['id']}/activate", headers=_auth_headers(login))
    return policy


async def _wait_for_pending_approval(login: dict[str, str], deadline_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        async with _http_client() as http:
            response = await http.get("/approvals", headers=_auth_headers(login))
        pending = response.json()
        if pending:
            return pending[0]
        await asyncio.sleep(0.05)
    raise AssertionError("no pending approval appeared in time")


async def test_authorization_policy_blocks_approval_over_cap_with_reason(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    approver = await make_user(role="approver")
    approver_login = await login_as(approver)

    tool_call_policy = await _create_and_activate(
        admin_login, f"u7-tool-call-{uuid.uuid4()}", REQUIRE_APPROVAL_FOR_PAYMENTS
    )
    await assign_policy_set_to_agent(agent_id, UUID(tool_call_policy["policy_set_id"]))
    # AUTHORIZATION_POLICY_SET_NAME is a reserved *name*, not a new
    # endpoint or table — created/activated through the exact same
    # POST /policies flow as any other policy, the whole point of this
    # design (one evaluator, two rule sets).
    await _create_and_activate(admin_login, AUTHORIZATION_POLICY_SET_NAME, BLOCK_APPROVALS_OVER_200)

    async def execute() -> str:
        return "charged"

    async with _bastion_client(agent_id, raw_key) as client:
        call_task = asyncio.create_task(client.call("payments.charge", {"amount": 250}, execute))
        approval = await _wait_for_pending_approval(approver_login)

        async with _http_client() as http:
            response = await http.post(
                f"/approvals/{approval['id']}/approve", headers=_auth_headers(approver_login)
            )

        assert response.status_code == 403
        body = response.json()
        assert body["error"]["code"] == "AUTHORIZATION_DENIED"
        # The "Why?" explanation: the exact same reason format any
        # tool-call block already produces (policy.py's evaluate(),
        # unmodified) — "blocked by policy rule for tool '<match.tool>'",
        # where match.tool is "approve" here since that's what this
        # authorization rule's match names. Same evaluator, same shape,
        # proven by literally getting the same string template back.
        assert body["error"]["message"] == "blocked by policy rule for tool 'approve'"

        # The underlying call is still genuinely pending — a denied
        # authorization attempt doesn't silently resolve anything.
        async with _http_client() as http:
            still_pending = await http.get("/approvals", headers=_auth_headers(approver_login))
        assert any(p["id"] == approval["id"] for p in still_pending.json())

        call_task.cancel()


async def test_authorization_policy_allows_approval_under_cap(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    approver = await make_user(role="approver")
    approver_login = await login_as(approver)

    tool_call_policy = await _create_and_activate(
        admin_login, f"u7-tool-call-{uuid.uuid4()}", REQUIRE_APPROVAL_FOR_PAYMENTS
    )
    await assign_policy_set_to_agent(agent_id, UUID(tool_call_policy["policy_set_id"]))
    await _create_and_activate(admin_login, AUTHORIZATION_POLICY_SET_NAME, BLOCK_APPROVALS_OVER_200)

    executed = False

    async def execute() -> str:
        nonlocal executed
        executed = True
        return "charged"

    async with _bastion_client(agent_id, raw_key) as client:
        call_task = asyncio.create_task(client.call("payments.charge", {"amount": 150}, execute))
        approval = await _wait_for_pending_approval(approver_login)

        async with _http_client() as http:
            response = await http.post(
                f"/approvals/{approval['id']}/approve", headers=_auth_headers(approver_login)
            )
        assert response.status_code == 200
        assert response.json()["status"] == "approved"

        result = await asyncio.wait_for(call_task, timeout=5)

    assert result == "charged"
    assert executed is True


async def test_no_authorization_policy_configured_preserves_v1_behavior(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    """No org-level authorization policy at all (the vast majority of orgs,
    including every one from before U7): approve/deny must behave exactly
    as it always did — RBAC role check only, no additional restriction."""
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    approver_login = await login_as(await make_user(role="approver"))

    tool_call_policy = await _create_and_activate(
        admin_login, f"u7-no-authz-{uuid.uuid4()}", REQUIRE_APPROVAL_FOR_PAYMENTS
    )
    await assign_policy_set_to_agent(agent_id, UUID(tool_call_policy["policy_set_id"]))

    async def execute() -> str:
        return "charged"

    async with _bastion_client(agent_id, raw_key) as client:
        # A large amount that WOULD be blocked if an authorization policy
        # like the other tests' existed — proving its absence really means
        # "no additional restriction," not "silently blocked by default."
        call_task = asyncio.create_task(client.call("payments.charge", {"amount": 999999}, execute))
        approval = await _wait_for_pending_approval(approver_login)

        async with _http_client() as http:
            response = await http.post(
                f"/approvals/{approval['id']}/approve", headers=_auth_headers(approver_login)
            )
        assert response.status_code == 200

        result = await asyncio.wait_for(call_task, timeout=5)
    assert result == "charged"
