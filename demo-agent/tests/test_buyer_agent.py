"""Track 01: the purchase scenarios (demo_agent/buyer_agent.py) against the
real interceptor (via ASGITransport, same pattern as test_scenario.py) and
the real catalog service (a genuine HTTP call — razorpay_tools.fetch_catalog
isn't swappable to ASGITransport the way interceptor calls are, since it's
a separate FastAPI app; requires `uv run --project catalog uvicorn
bastion_catalog.main:app --port 4003` running, same as CI's "Start catalog
service" step).
"""

from __future__ import annotations

import httpx
import pytest
from bastion import BastionClient
from bastion_interceptor.main import app as interceptor_app
from demo_agent.buyer_agent import (
    RATE_LIMIT_PER_MINUTE,
    run_purchase_burst_scenario,
    run_purchase_scenario,
)
from demo_agent.seed import AGENT_API_KEY, AGENT_ID


def _client() -> BastionClient:
    return BastionClient(
        base_url="http://interceptor.test",
        api_key=AGENT_API_KEY,
        agent_id=AGENT_ID,
        transport=httpx.ASGITransport(app=interceptor_app),
    )


@pytest.fixture(autouse=True)
async def _require_catalog_service() -> None:
    """A clear, actionable skip rather than an opaque connection-refused
    failure if someone runs this file without the catalog service up."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as http:
            response = await http.get("http://localhost:4003/healthz")
        response.raise_for_status()
    except httpx.HTTPError:
        pytest.skip("catalog service not reachable on :4003 — start it before running this file")


async def test_purchase_scenario_returns_a_labeled_receipt_and_folds_into_events() -> None:
    client = _client()
    try:
        result = await run_purchase_scenario(client)
    finally:
        await client.aclose()

    assert result.blocked is False
    assert result.receipt is not None
    assert result.receipt["order_id"].startswith("order_")
    assert result.receipt["payment_id"].startswith("pay_")
    assert result.receipt["status"] == "captured"
    # No Razorpay test-mode credentials in this environment (see
    # razorpay_tools.py's module docstring) — the receipt must say so.
    assert result.receipt["simulated"] is True


async def test_burst_scenario_hits_the_rate_limit() -> None:
    client = _client()
    try:
        result = await run_purchase_burst_scenario(client, attempts=RATE_LIMIT_PER_MINUTE + 3)
    finally:
        await client.aclose()

    assert result.blocked_count > 0
    assert result.allowed_count <= RATE_LIMIT_PER_MINUTE
    blocked_reasons = [a.reason for a in result.attempts if not a.allowed]
    assert all("calls_per_minute" in (r or "") for r in blocked_reasons)
