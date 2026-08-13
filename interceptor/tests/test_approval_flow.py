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


async def _create_and_assign_approval_policy(
    test_org: UUID,
    agent_id: UUID,
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
    name: str,
) -> None:
    async with _http_client() as http:
        created = await http.post(
            "/policies",
            json={
                "org_id": str(test_org),
                "name": name,
                "definition": REQUIRE_APPROVAL_FOR_PAYMENTS,
            },
        )
        policy = created.json()
        await http.post(f"/policies/{policy['id']}/activate")
    await assign_policy_set_to_agent(agent_id, UUID(policy["policy_set_id"]))


async def _wait_for_pending_approval(org_id: UUID, deadline_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        async with _http_client() as http:
            response = await http.get("/approvals", params={"org_id": str(org_id)})
        pending = response.json()
        if pending:
            return pending[0]
        await asyncio.sleep(0.05)
    raise AssertionError("no pending approval appeared in time")


async def test_approval_flow_pauses_and_resumes_on_approve(
    test_org: UUID,
    test_agent: tuple[UUID, str],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    await _create_and_assign_approval_policy(
        test_org, agent_id, assign_policy_set_to_agent, "approve-flow"
    )

    executed = False

    async def execute() -> str:
        nonlocal executed
        executed = True
        return "charged"

    async with _bastion_client(agent_id, raw_key) as client:
        call_task = asyncio.create_task(client.call("payments.charge", {"amount": 100}, execute))

        approval = await _wait_for_pending_approval(test_org)
        # The call is genuinely paused, not just "about to return" — execute()
        # must not have run yet, and the task must still be in flight.
        assert executed is False
        assert not call_task.done()

        async with _http_client() as http:
            approve_response = await http.post(f"/approvals/{approval['id']}/approve")
        assert approve_response.status_code == 200
        assert approve_response.json()["status"] == "approved"

        result = await asyncio.wait_for(call_task, timeout=5)

    assert result == "charged"
    assert executed is True


async def test_approval_flow_denied_by_human(
    test_org: UUID,
    test_agent: tuple[UUID, str],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    await _create_and_assign_approval_policy(
        test_org, agent_id, assign_policy_set_to_agent, "deny-flow"
    )

    executed = False

    async def execute() -> str:
        nonlocal executed
        executed = True
        return "charged"

    async with _bastion_client(agent_id, raw_key) as client:
        call_task = asyncio.create_task(client.call("payments.charge", {"amount": 100}, execute))

        approval = await _wait_for_pending_approval(test_org)

        async with _http_client() as http:
            deny_response = await http.post(f"/approvals/{approval['id']}/deny")
        assert deny_response.status_code == 200
        assert deny_response.json()["status"] == "denied"

        with pytest.raises(BastionBlockedError, match="denied"):
            await asyncio.wait_for(call_task, timeout=5)

    # execute() must never run for a denied call — this is the actual
    # prevention mechanism, not just advisory.
    assert executed is False


async def test_approval_timeout_denies_after_ttl(
    test_org: UUID,
    test_agent: tuple[UUID, str],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    await _create_and_assign_approval_policy(
        test_org, agent_id, assign_policy_set_to_agent, "timeout-flow"
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
