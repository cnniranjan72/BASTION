"""Phase 6 milestone (BUILD_PLAN.md): two browser tabs open, agent runs,
both see identical live updates with no polling.

Uses httpx-ws (not Starlette's own sync TestClient.websocket_connect) so the
WebSocket runs on the *same* event loop as the rest of this async test suite
— TestClient's websocket_connect drives the ASGI app on a separate thread's
event loop, which would hit the same cross-loop asyncpg issue documented in
the root pyproject.toml (the session-scoped db pool is bound to the outer
loop).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from bastion import BastionClient
from bastion_aggregator.db import db
from bastion_aggregator.main import app as aggregator_app
from bastion_interceptor.main import app as interceptor_app
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport


def _bastion_client(agent_id: UUID, raw_key: str) -> BastionClient:
    return BastionClient(
        base_url="http://interceptor.test",
        api_key=raw_key,
        agent_id=agent_id,
        transport=httpx.ASGITransport(app=interceptor_app),
    )


async def _noop() -> str:
    return "ok"


async def test_two_viewers_see_identical_live_updates_with_no_polling(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
) -> None:
    agent_id, raw_key = test_agent
    login_a = await login_as(await make_user(role="viewer"))
    login_b = await login_as(await make_user(role="viewer"))

    transport = ASGIWebSocketTransport(app=aggregator_app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://aggregator.test") as client_a,
        httpx.AsyncClient(transport=transport, base_url="http://aggregator.test") as client_b,
        aconnect_ws(f"/live/{agent_id}?token={login_a['access_token']}", client_a) as ws_a,
        aconnect_ws(f"/live/{agent_id}?token={login_b['access_token']}", client_b) as ws_b,
    ):
        # Give both handshakes a moment to complete before generating traffic.
        await asyncio.sleep(0.1)

        async with _bastion_client(agent_id, raw_key) as client:
            await client.call("tool.live-test", {}, _noop)

        messages_a = [await ws_a.receive_json() for _ in range(3)]
        messages_b = [await ws_b.receive_json() for _ in range(3)]

    assert messages_a == messages_b

    types = [m["type"] for m in messages_a]
    assert types == ["node_added", "node_updated", "node_updated"]
    assert messages_a[0]["node"]["tool_name"] == "tool.live-test"
    assert messages_a[0]["node"]["status"] == "pending"
    assert messages_a[1]["status"] == "allowed"
    assert messages_a[2]["status"] == "completed"
    assert messages_a[2]["latency_ms"] is not None


async def test_missing_token_closes_connection(test_agent: tuple[UUID, str]) -> None:
    agent_id, _ = test_agent
    transport = ASGIWebSocketTransport(app=aggregator_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://aggregator.test") as client:
        try:
            async with aconnect_ws(f"/live/{agent_id}", client) as ws:
                await ws.receive_json()
        except Exception:
            return
    raise AssertionError("expected the connection to be rejected")


async def test_cross_org_agent_id_closes_connection(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
) -> None:
    agent_id, _ = test_agent
    other_org = await db.pool.fetchval(
        "INSERT INTO organizations (id, name) VALUES (gen_random_uuid(), 'org-b-ws') RETURNING id"
    )
    other_org_login = await login_as(await make_user(role="viewer", org_id=other_org))
    transport = ASGIWebSocketTransport(app=aggregator_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://aggregator.test") as client:
        try:
            async with aconnect_ws(
                f"/live/{agent_id}?token={other_org_login['access_token']}", client
            ) as ws:
                await ws.receive_json()
        except Exception:
            return
    raise AssertionError("expected the connection to be rejected")
