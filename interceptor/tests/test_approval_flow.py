"""Phase 3 milestone (BUILD_PLAN.md): a blocked-pending-approval call
actually pauses SDK execution and resumes correctly after a human clicks
approve, including the timeout-denies case.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
import pytest
from bastion import BastionBlockedError, BastionClient
from bastion_interceptor.config import config as interceptor_config
from bastion_interceptor.main import app

REQUIRE_APPROVAL_FOR_PAYMENTS = [
    {"match": {"tool": "payments.charge"}, "action": "require_approval"},
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


async def _create_and_assign_approval_policy(
    login: dict[str, str],
    agent_id: UUID,
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
    name: str,
) -> None:
    async with _http_client() as http:
        created = await http.post(
            "/policies",
            json={"name": name, "definition": REQUIRE_APPROVAL_FOR_PAYMENTS},
            headers=_auth_headers(login),
        )
        policy = created.json()
        await http.post(f"/policies/{policy['id']}/activate", headers=_auth_headers(login))
    await assign_policy_set_to_agent(agent_id, UUID(policy["policy_set_id"]))


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


async def test_approval_flow_pauses_and_resumes_on_approve(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    await _create_and_assign_approval_policy(
        admin_login, agent_id, assign_policy_set_to_agent, "approve-flow"
    )
    approver = await make_user(role="approver")
    approver_login = await login_as(approver)

    executed = False

    async def execute() -> str:
        nonlocal executed
        executed = True
        return "charged"

    async with _bastion_client(agent_id, raw_key) as client:
        call_task = asyncio.create_task(client.call("payments.charge", {"amount": 100}, execute))

        approval = await _wait_for_pending_approval(approver_login)
        # The call is genuinely paused, not just "about to return" — execute()
        # must not have run yet, and the task must still be in flight.
        assert executed is False
        assert not call_task.done()

        async with _http_client() as http:
            approve_response = await http.post(
                f"/approvals/{approval['id']}/approve", headers=_auth_headers(approver_login)
            )
        assert approve_response.status_code == 200
        assert approve_response.json()["status"] == "approved"
        assert approve_response.json()["resolved_by"] == str(approver["id"])

        result = await asyncio.wait_for(call_task, timeout=5)

    assert result == "charged"
    assert executed is True


async def test_approval_flow_denied_by_human(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    await _create_and_assign_approval_policy(
        admin_login, agent_id, assign_policy_set_to_agent, "deny-flow"
    )
    approver_login = await login_as(await make_user(role="approver"))

    executed = False

    async def execute() -> str:
        nonlocal executed
        executed = True
        return "charged"

    async with _bastion_client(agent_id, raw_key) as client:
        call_task = asyncio.create_task(client.call("payments.charge", {"amount": 100}, execute))

        approval = await _wait_for_pending_approval(approver_login)

        async with _http_client() as http:
            deny_response = await http.post(
                f"/approvals/{approval['id']}/deny", headers=_auth_headers(approver_login)
            )
        assert deny_response.status_code == 200
        assert deny_response.json()["status"] == "denied"

        with pytest.raises(BastionBlockedError, match="denied"):
            await asyncio.wait_for(call_task, timeout=5)

    # execute() must never run for a denied call — this is the actual
    # prevention mechanism, not just advisory.
    assert executed is False


async def test_viewer_cannot_approve(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    admin = await make_user(role="admin")
    admin_login = await login_as(admin)
    await _create_and_assign_approval_policy(
        admin_login, agent_id, assign_policy_set_to_agent, "viewer-forbidden-flow"
    )

    async def execute() -> str:
        return "charged"

    viewer = await make_user(role="viewer")
    viewer_login = await login_as(viewer)

    async with _bastion_client(agent_id, raw_key) as client:
        call_task = asyncio.create_task(client.call("payments.charge", {"amount": 100}, execute))
        approval = await _wait_for_pending_approval(admin_login)

        async with _http_client() as http:
            response = await http.post(
                f"/approvals/{approval['id']}/approve", headers=_auth_headers(viewer_login)
            )
        assert response.status_code == 403

        # Clean up the still-pending call so the test doesn't leak a task
        # waiting on the full long-poll/TTL window.
        async with _http_client() as http:
            await http.post(f"/approvals/{approval['id']}/deny", headers=_auth_headers(admin_login))
        with pytest.raises(BastionBlockedError):
            await asyncio.wait_for(call_task, timeout=5)


async def test_approval_timeout_denies_after_ttl(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    await _create_and_assign_approval_policy(
        admin_login, agent_id, assign_policy_set_to_agent, "timeout-flow"
    )

    # Frozen dataclass: object.__setattr__ is the standard escape hatch for
    # test-only mutation. Restored in `finally` — this is the shared
    # singleton every request handler reads from, not a copy.
    original_ttl = interceptor_config.approval_ttl_seconds
    original_poll = interceptor_config.approval_long_poll_seconds
    object.__setattr__(interceptor_config, "approval_ttl_seconds", 0.3)
    object.__setattr__(interceptor_config, "approval_long_poll_seconds", 0.2)
    try:
        executed = False

        async def execute() -> str:
            nonlocal executed
            executed = True
            return "charged"

        async with _bastion_client(agent_id, raw_key) as client:
            with pytest.raises(BastionBlockedError, match="timed_out"):
                await client.call("payments.charge", {"amount": 100}, execute)

        assert executed is False
    finally:
        object.__setattr__(interceptor_config, "approval_ttl_seconds", original_ttl)
        object.__setattr__(interceptor_config, "approval_long_poll_seconds", original_poll)
