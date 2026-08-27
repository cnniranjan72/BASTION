"""BastionClient._wait_for_approval's transport-error handling.

A real network blip (connection reset, DNS failure) mid-poll against
GET /approvals/{id} previously raised a raw httpx exception instead of
the documented fail-closed guarantee — the timeout branch already failed
closed correctly, this was the one gap between that promise and what
actually happened on a network failure specifically. No real interceptor
needed here: an httpx.MockTransport is enough to simulate the failure
deterministically, without a flaky real-network dependency.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from bastion import BastionBlockedError, BastionClient


def _transport_error_mid_poll() -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/intercept":
            return httpx.Response(
                200,
                json={
                    "span_id": str(uuid4()),
                    "decision": "pending_approval",
                    "approval_request_id": str(uuid4()),
                    "poll_url": "/approvals/does-not-matter",
                },
            )
        if request.url.path.startswith("/approvals/"):
            raise httpx.ConnectError("simulated connection reset", request=request)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return httpx.MockTransport(handler)


async def test_transport_error_mid_approval_poll_fails_closed_not_raw_exception() -> None:
    client = BastionClient(
        base_url="http://interceptor.test",
        api_key="test-key",
        agent_id=uuid4(),
        transport=_transport_error_mid_poll(),
    )
    try:
        with pytest.raises(BastionBlockedError, match="approval poll failed"):
            await client.call("payments.charge", {"amount": 10}, lambda: None)
    finally:
        await client.aclose()
