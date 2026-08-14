"""U11 milestone tests (UPGRADE_BUILD_PLAN.md):

1. Client A connects to gateway 1, client B connects to gateway 2, an
   event originates and is delivered correctly to both.
2. Flood the system with a burst of events and assert the dashboard
   doesn't fall behind or crash — measure actual propagation latency
   under the burst.

Two independent `ConnectionManager` instances stand in for "gateway 1"/
"gateway 2" — genuinely separate Python objects with zero shared
in-memory state, connected only through the real Redis instance
(`bastion_aggregator.redis_bus.redis_bus`, the same one the app's own
module-level `manager` singleton uses). This proves the actual mechanism
(any gateway instance can deliver to any client, because delivery only
ever happens via a Redis subscription, never a direct in-process call)
without the operational complexity of spinning up two real OS processes —
the same "independent instances, real shared infrastructure" pattern
already established for Kafka multi-consumer proofs in U3.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any
from uuid import UUID

from bastion_aggregator.ws import ConnectionManager
from bastion_shared import LiveNode, NodeAddedMessage, NodeUpdatedMessage


class _FakeWebSocket:
    """A minimal stand-in for fastapi.WebSocket — ConnectionManager only
    ever calls .send_json() on a connected socket (accept() happens in the
    real WS endpoint, not inside ConnectionManager itself, so it's not
    needed here)."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.received.append(payload)


async def test_broadcast_from_one_gateway_reaches_clients_on_both(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, _ = test_agent

    # Two genuinely independent instances — no shared Python state at all,
    # only the real Redis pub/sub channel connects them.
    gateway_1 = ConnectionManager(batch_window_seconds=0)
    gateway_2 = ConnectionManager(batch_window_seconds=0)

    client_a = _FakeWebSocket()
    client_b = _FakeWebSocket()
    gateway_1._connections[agent_id].add(client_a)  # type: ignore[index]
    gateway_2._connections[agent_id].add(client_b)  # type: ignore[index]
    gateway_1._subscriptions[agent_id] = asyncio.create_task(gateway_1._subscribe_loop(agent_id))
    gateway_2._subscriptions[agent_id] = asyncio.create_task(gateway_2._subscribe_loop(agent_id))
    await asyncio.sleep(0.2)  # let both subscriptions establish

    try:
        span_id = uuid.uuid4()
        message = NodeAddedMessage(
            node=LiveNode(span_id=span_id, tool_name="fanout.test", status="pending")
        )
        # Published via gateway_1 only — gateway_2 never touches this call
        # at all, yet its own client must still receive it.
        await gateway_1.broadcast(agent_id, message)

        for _ in range(50):
            if client_a.received and client_b.received:
                break
            await asyncio.sleep(0.1)

        assert len(client_a.received) == 1
        assert len(client_b.received) == 1
        assert client_a.received == client_b.received
        assert client_a.received[0]["node"]["span_id"] == str(span_id)
    finally:
        for gw in (gateway_1, gateway_2):
            task = gw._subscriptions.pop(agent_id, None)
            if task is not None:
                task.cancel()


async def test_burst_of_rapid_updates_coalesces_and_measures_propagation_latency(
    test_agent: tuple[UUID, str],
) -> None:
    """A burst of 200 rapid status updates for the same node, all landing
    within one coalescing window, must be delivered as far fewer than 200
    messages (proving the dashboard doesn't fall behind / get flooded) —
    while the client still ends up with the correct final state, and the
    end-to-end propagation latency for that final state stays bounded."""
    agent_id, _ = test_agent
    batch_window_seconds = 0.1
    manager = ConnectionManager(batch_window_seconds=batch_window_seconds)

    client = _FakeWebSocket()
    manager._connections[agent_id].add(client)  # type: ignore[index]
    manager._subscriptions[agent_id] = asyncio.create_task(manager._subscribe_loop(agent_id))
    await asyncio.sleep(0.2)

    try:
        span_id = uuid.uuid4()
        burst_size = 200
        for i in range(burst_size):
            await manager.broadcast(
                agent_id,
                NodeUpdatedMessage(span_id=span_id, status="allowed" if i % 2 == 0 else "failed"),
            )
        # The final, real state — must be what the client ultimately sees.
        await manager.broadcast(agent_id, NodeUpdatedMessage(span_id=span_id, status="completed"))
        publish_done = time.perf_counter()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if client.received and client.received[-1]["status"] == "completed":
                break
            await asyncio.sleep(0.02)
        propagation_latency_s = time.perf_counter() - publish_done

        assert client.received, "client never received anything from the burst"
        assert client.received[-1]["status"] == "completed"
        # The real point: far fewer delivered messages than the 201 raw
        # updates published — coalescing is doing real work, not just
        # present in the code without effect.
        assert len(client.received) < burst_size / 4
        # Propagation latency for the final state is bounded by roughly
        # the coalescing window, not by the burst size — proving the
        # dashboard doesn't fall behind as volume grows (the actual
        # "doesn't fall over" claim this milestone test exists to check).
        assert propagation_latency_s < batch_window_seconds * 5
    finally:
        task = manager._subscriptions.pop(agent_id, None)
        if task is not None:
            task.cancel()
