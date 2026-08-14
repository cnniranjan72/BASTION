"""U6 milestone tests (UPGRADE_BUILD_PLAN.md):

1. Force a downstream tool to fail repeatedly, assert the breaker opens and
   subsequent calls fail fast without hitting the downstream; assert it
   half-opens after timeout and closes again on a successful probe.
2. Assert a policy with a $100/transaction cap correctly blocks a $150 call
   and allows a $50 one.

Plus direct coverage of the other real (not stubbed) `limits:` dimensions —
calls_per_minute and org_spend_per_day — proving they're working
enforcement, not declared-but-inert fields (see ADR-015 for the two
dimensions deliberately NOT implemented: tool-call-count budget and
runtime budget).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
import pytest
from bastion import BastionBlockedError, BastionClient
from bastion_interceptor import circuit_breaker
from bastion_interceptor.main import app
from bastion_interceptor.redis_bus import redis_bus


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
    definition: list[dict],
) -> None:
    async with _http_client() as http:
        created = await http.post(
            "/policies",
            json={"name": f"u6-test-{uuid.uuid4()}", "definition": definition},
            headers=_auth_headers(login),
        )
        policy = created.json()
        await http.post(f"/policies/{policy['id']}/activate", headers=_auth_headers(login))
    await assign_policy_set_to_agent(agent_id, UUID(policy["policy_set_id"]))


async def test_circuit_breaker_opens_fails_fast_then_half_opens_and_closes(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    tool_name = f"flaky-{uuid.uuid4()}"
    await _create_and_assign_policy(
        admin_login,
        agent_id,
        assign_policy_set_to_agent,
        [{"match": {"tool": "*"}, "action": "allow"}],
    )

    async def failing_execute() -> str:
        raise RuntimeError("downstream is down")

    async with _bastion_client(agent_id, raw_key) as client:
        # Drive it to OPEN: enough consecutive failures to cross the
        # default threshold. Each of these genuinely invokes execute() —
        # the breaker hasn't tripped yet, policy already allowed the call.
        for _ in range(circuit_breaker.DEFAULT_FAILURE_THRESHOLD):
            with pytest.raises(RuntimeError):
                await client.call(tool_name, {}, failing_execute)

        # Breaker now OPEN: the next call must fail fast as a policy-style
        # block (BastionBlockedError), and — the actual point of a circuit
        # breaker — execute() must never run at all.
        executed = False

        async def should_not_run() -> str:
            nonlocal executed
            executed = True
            return "unreachable"

        with pytest.raises(BastionBlockedError) as exc_info:
            await client.call(tool_name, {}, should_not_run)
        assert executed is False
        assert "circuit breaker open" in (exc_info.value.reason or "")

        # Simulate open_timeout_seconds having elapsed (30s by default —
        # not worth actually sleeping for in a test) by backdating
        # opened_at directly, the same Redis key is_open() itself reads.
        await redis_bus.raw_client.set(circuit_breaker._key(agent_id, tool_name, "opened_at"), "0")

        # HALF_OPEN: this call is let through as a trial probe. A
        # successful outcome must close the breaker.
        async def succeeding_execute() -> str:
            return "ok"

        result = await client.call(tool_name, {}, succeeding_execute)
        assert result == "ok"

        # Confirm it's genuinely closed, not still hovering in HALF_OPEN —
        # a normal follow-up call succeeds with no special handling.
        result2 = await client.call(tool_name, {}, succeeding_execute)
        assert result2 == "ok"


async def test_circuit_breaker_reopens_if_half_open_probe_also_fails(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    tool_name = f"flaky-reopen-{uuid.uuid4()}"
    await _create_and_assign_policy(
        admin_login,
        agent_id,
        assign_policy_set_to_agent,
        [{"match": {"tool": "*"}, "action": "allow"}],
    )

    async def failing_execute() -> str:
        raise RuntimeError("still down")

    async with _bastion_client(agent_id, raw_key) as client:
        for _ in range(circuit_breaker.DEFAULT_FAILURE_THRESHOLD):
            with pytest.raises(RuntimeError):
                await client.call(tool_name, {}, failing_execute)

        await redis_bus.raw_client.set(circuit_breaker._key(agent_id, tool_name, "opened_at"), "0")

        # The trial probe itself fails — must go straight back to OPEN,
        # not stay HALF_OPEN indefinitely letting every call through.
        with pytest.raises(RuntimeError):
            await client.call(tool_name, {}, failing_execute)

        state = await redis_bus.raw_client.get(circuit_breaker._key(agent_id, tool_name, "state"))
        assert state == "OPEN"


async def test_max_transaction_amount_blocks_over_cap_allows_under(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    await _create_and_assign_policy(
        admin_login,
        agent_id,
        assign_policy_set_to_agent,
        [
            {
                "match": {"tool": "payments.transfer"},
                "action": "allow",
                "limits": {"max_transaction_amount": 100},
            },
        ],
    )

    async def execute() -> str:
        return "done"

    async with _bastion_client(agent_id, raw_key) as client:
        with pytest.raises(BastionBlockedError) as exc_info:
            await client.call("payments.transfer", {"amount": 150}, execute)
        assert "max_transaction_amount" in (exc_info.value.reason or "")

        result = await client.call("payments.transfer", {"amount": 50}, execute)
        assert result == "done"


async def test_calls_per_minute_limit_blocks_once_exceeded(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    await _create_and_assign_policy(
        admin_login,
        agent_id,
        assign_policy_set_to_agent,
        [
            {
                "match": {"tool": "reports.generate"},
                "action": "allow",
                "limits": {"calls_per_minute": 2},
            },
        ],
    )

    async def execute() -> str:
        return "done"

    async with _bastion_client(agent_id, raw_key) as client:
        assert await client.call("reports.generate", {}, execute) == "done"
        assert await client.call("reports.generate", {}, execute) == "done"
        with pytest.raises(BastionBlockedError) as exc_info:
            await client.call("reports.generate", {}, execute)
        assert "calls_per_minute" in (exc_info.value.reason or "")


async def test_org_spend_per_day_blocks_once_cumulative_amount_exceeds_cap(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
    assign_policy_set_to_agent: Callable[[UUID, UUID], Awaitable[None]],
) -> None:
    agent_id, raw_key = test_agent
    admin_login = await login_as(await make_user(role="admin"))
    await _create_and_assign_policy(
        admin_login,
        agent_id,
        assign_policy_set_to_agent,
        [
            {
                "match": {"tool": "payments.transfer"},
                "action": "allow",
                "limits": {"org_spend_per_day": 100},
            },
        ],
    )

    async def execute() -> str:
        return "done"

    async with _bastion_client(agent_id, raw_key) as client:
        assert await client.call("payments.transfer", {"amount": 60}, execute) == "done"
        # 60 + 60 = 120 > 100: this one must be rejected even though no
        # single transaction alone exceeds anything — it's the cumulative
        # org-wide total across separate calls that's being governed here.
        with pytest.raises(BastionBlockedError) as exc_info:
            await client.call("payments.transfer", {"amount": 60}, execute)
        assert "org_spend_per_day" in (exc_info.value.reason or "")
        # Room remains under the cap (60 already spent, cap 100): a smaller
        # call still succeeds.
        assert await client.call("payments.transfer", {"amount": 30}, execute) == "done"
