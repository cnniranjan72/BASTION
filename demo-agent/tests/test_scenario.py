"""Phase 8 milestone (BUILD_PLAN.md): "Script a scenario: a document the
agent reads contains an injected instruction (...), BASTION's policy blocks
it... This is your interview demo. It must be reliable — run it 20 times,
make sure it's not flaky." Both assertions live here, against the real
interceptor app (via ASGITransport) and real Postgres — same cross-service
pattern as aggregator/tests/test_replay.py.
"""

from __future__ import annotations

import httpx
from bastion import BastionClient
from bastion_interceptor.main import app as interceptor_app
from demo_agent.agent import run_prompt_injection_scenario
from demo_agent.seed import AGENT_API_KEY, AGENT_ID


def _client() -> BastionClient:
    return BastionClient(
        base_url="http://interceptor.test",
        api_key=AGENT_API_KEY,
        agent_id=AGENT_ID,
        transport=httpx.ASGITransport(app=interceptor_app),
    )


async def test_injected_transfer_is_blocked_but_legit_transfer_is_not() -> None:
    client = _client()
    try:
        result = await run_prompt_injection_scenario(client)
    finally:
        await client.aclose()

    assert result.ticket is not None
    assert result.injected_transfer_attempted is True
    assert result.injected_transfer_blocked is True
    assert result.block_reason is not None

    # The policy targets the amount, not the tool wholesale — a legitimate
    # small transfer in the same trace still goes through.
    assert result.legit_refund is not None
    assert result.legit_refund["status"] == "sent"


async def test_injected_transfer_is_blocked_reliably_across_20_runs() -> None:
    """BUILD_PLAN.md's own reliability bar, checked directly: 20 sequential
    runs of the same deterministic scenario, every one must block."""
    outcomes = []
    for _ in range(20):
        client = _client()
        try:
            result = await run_prompt_injection_scenario(client)
        finally:
            await client.aclose()
        outcomes.append(result.injected_transfer_blocked)

    assert outcomes == [True] * 20
