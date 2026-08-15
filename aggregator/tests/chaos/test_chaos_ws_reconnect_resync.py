"""U14 chaos scenario (UPGRADE_ARCHITECTURE.md §16): "Drop WebSocket
connection mid-session" — required invariant: "client reconnects and
correctly resyncs current graph state (not just future deltas)."

Real finding from writing this test (fixed, not just documented): before
this phase, WS /live/{agent_id} (aggregator/src/bastion_aggregator/main.py's
`live()` route) only ever registered the new connection and waited — a
reconnecting client saw nothing but whatever happened *after* it
reconnected. Anything that happened while it was disconnected was silently
lost, because ws.py's ConnectionManager only ever broadcasts forward to
already-subscribed connections. Fixed by adding `_send_resync_snapshot`
(main.py), which replays `active_traces` for the connecting agent as the
same delta message types a live client already knows how to reduce
(node_added [+ edge_added], then node_updated to the real current status)
the instant a new connection is accepted — see main.py's `live()` for the
connect-before-snapshot ordering tradeoff this makes explicit.

Scope, stated explicitly: this only resyncs traces still *running* as of
reconnect time. A trace that reached a terminal state while the client was
disconnected is evicted from `active_traces` (main.py's
`_handle_notification`) and isn't resynced over the live channel — by
design, a fully-completed trace is what GET /traces/{id} is for; the live
channel's job is in-flight state, not history. This test's scenario is
therefore deliberately a still-running trace, the case this fix actually
targets.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from bastion_aggregator.main import app as aggregator_app
from bastion_interceptor.db import db as interceptor_db
from bastion_interceptor.main import app as interceptor_app
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport


async def test_client_reconnecting_after_a_drop_resyncs_state_missed_while_disconnected(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
) -> None:
    agent_id, raw_key = test_agent
    login = await login_as(await make_user(role="viewer"))
    transport = ASGIWebSocketTransport(app=aggregator_app)

    # First connection: nothing has happened yet, so the resync snapshot is
    # empty. Disconnect immediately afterward — everything interesting
    # happens strictly *after* this client is gone.
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://aggregator.test") as client_a,
        aconnect_ws(f"/live/{agent_id}?token={login['access_token']}", client_a),
    ):
        await asyncio.sleep(0.05)  # let the handshake + empty resync settle
    # ws_a is now closed — genuinely gone, not just idle.

    # Generate a still-running trace entirely while no client is connected:
    # POST /intercept writes CallAttempted + CallAllowed and returns; no
    # POST /spans/{id}/complete is ever sent, so the trace stays "running"
    # (see fold_events_to_graph: status is "running" until the root span
    # reaches a terminal event).
    trace_id = uuid.uuid4()
    body = {
        "trace_id": str(trace_id),
        "parent_span_id": None,
        "tool_name": "chaos.ws_resync_test",
        "args": {},
        "agent_id": str(agent_id),
        "idempotency_key": str(uuid.uuid4()),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=interceptor_app), base_url="http://interceptor.test"
    ) as http:
        response = await http.post(
            "/intercept", json=body, headers={"Authorization": f"Bearer {raw_key}"}
        )
    assert response.status_code == 200, response.text

    # Wait for the real pipeline (outbox -> Kafka -> aggregator consumer)
    # to actually process this before reconnecting — otherwise the
    # reconnect could race the write and the test would be non-deterministic.
    deadline = asyncio.get_event_loop().time() + 10
    events = []
    while asyncio.get_event_loop().time() < deadline:
        events = await interceptor_db.get_events_for_trace(trace_id)
        if len(events) >= 2:
            break
        await asyncio.sleep(0.1)
    assert len(events) >= 2, "expected CallAttempted + CallAllowed to land within 10s"
    await asyncio.sleep(0.5)  # give _handle_notification a moment to update active_traces

    # A fresh connection — standing in for the same client reconnecting
    # after its drop — must see the full current state as its very first
    # messages, without ever having been connected while the events above
    # actually happened.
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://aggregator.test") as client_b,
        aconnect_ws(f"/live/{agent_id}?token={login['access_token']}", client_b) as ws_b,
    ):
        first = await asyncio.wait_for(ws_b.receive_json(), timeout=5)
        second = await asyncio.wait_for(ws_b.receive_json(), timeout=5)

    assert first["type"] == "node_added", first
    assert first["node"]["tool_name"] == "chaos.ws_resync_test"
    assert second["type"] == "node_updated", second
    assert second["status"] == "allowed"
    assert second["span_id"] == first["node"]["span_id"]
