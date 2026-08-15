"""Live graph-delta fan-out over WebSocket — ARCHITECTURE.md §2.5/§2.6,
BUILD_PLAN.md Phase 6. Connections are grouped by agent_id (matching
`WS /live/{agent_id}` in API_SPEC.md): a viewer only ever sees deltas for
the one agent they subscribed to.

U11 (v2 upgrade), UPGRADE_ARCHITECTURE.md §13: v1's design broadcast
straight from the in-process connection set, which only ever reached
clients connected to *this* process — the moment there's a second WS
gateway instance, a client on gateway 2 never learns about an event
gateway 1 processed. `broadcast()` now *publishes* to Redis instead of
iterating local connections directly; `_subscribe_loop` (one per agent_id
with at least one local connection) is the only path that ever calls
`_deliver_locally` — on this instance or any other, local delivery always
happens the same way, in response to a Redis message, never as a direct
side effect of the Kafka-consumer callback that produced it. This is what
makes "any gateway instance can serve any client" true instead of
aspirational.

Backpressure (§13's "An agent producing 100,000 events/sec..." scenario):
`_enqueue` coalesces multiple `NodeUpdatedMessage`s for the same span_id
arriving within `batch_window_seconds` into just the latest one — a burst
of rapid status changes for one node is never delivered as N separate
messages, only the final state that's still true by the time the window
flushes. `NodeAddedMessage`/`EdgeAddedMessage` are structural, never
coalesced. `batch_window_seconds=0` (or config.ws_batch_window_seconds set
to 0) disables coalescing entirely — immediate, one message per update,
the pre-U11 behavior every exact-sequence test predating this phase relies
on.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from uuid import UUID

from bastion_shared import LiveMessage, NodeUpdatedMessage
from fastapi import WebSocket

from .config import config
from .logging import log
from .redis_bus import redis_bus


def _coalescing_key(message: LiveMessage) -> str | None:
    """None means "never coalesced, always delivered on its own" — only
    NodeUpdatedMessage represents a *replaceable* current-state update;
    NodeAddedMessage/EdgeAddedMessage are one-time structural facts."""
    if isinstance(message, NodeUpdatedMessage):
        return str(message.span_id)
    return None


class ConnectionManager:
    def __init__(self, *, batch_window_seconds: float | None = None) -> None:
        self._connections: dict[UUID, set[WebSocket]] = defaultdict(set)
        self._batch_window_seconds = (
            batch_window_seconds
            if batch_window_seconds is not None
            else config.ws_batch_window_seconds
        )
        self._subscriptions: dict[UUID, asyncio.Task[None]] = {}
        # Ordered per-agent pending list: (coalescing_key_or_None, message).
        # A coalescing_key entry is replaced in place on a later arrival
        # (keeping its original position — order reflects when a node was
        # *last* touched, not re-touched-to-the-back); a None-keyed entry
        # is always appended fresh.
        self._pending: dict[UUID, list[tuple[str | None, LiveMessage]]] = defaultdict(list)
        self._flush_tasks: dict[UUID, asyncio.Task[None]] = {}

    async def connect(self, agent_id: UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[agent_id].add(websocket)
        if agent_id not in self._subscriptions:
            self._subscriptions[agent_id] = asyncio.create_task(self._subscribe_loop(agent_id))

    def disconnect(self, agent_id: UUID, websocket: WebSocket) -> None:
        connections = self._connections.get(agent_id)
        if connections is None:
            return
        connections.discard(websocket)
        if not connections:
            self._connections.pop(agent_id, None)
            task = self._subscriptions.pop(agent_id, None)
            if task is not None:
                task.cancel()

    def connection_count(self) -> int:
        """U12 (v2 upgrade): total WS clients connected on this instance,
        across every agent_id — main.py's /metrics samples this."""
        return sum(len(conns) for conns in self._connections.values())

    async def broadcast(self, agent_id: UUID, message: LiveMessage) -> None:
        """Called by main.py's _handle_notification — publishes only.
        Local delivery, on this instance or any other, always happens via
        _subscribe_loop below, never directly here."""
        await redis_bus.publish_live_message(agent_id, message)

    async def _subscribe_loop(self, agent_id: UUID) -> None:
        try:
            async for message in redis_bus.subscribe_live_messages(agent_id):
                await self._enqueue(agent_id, message)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("live message subscription failed", agent_id=str(agent_id))

    async def _enqueue(self, agent_id: UUID, message: LiveMessage) -> None:
        if self._batch_window_seconds <= 0:
            await self._deliver_locally(agent_id, [message])
            return

        key = _coalescing_key(message)
        pending = self._pending[agent_id]
        if key is not None:
            for i, (existing_key, _) in enumerate(pending):
                if existing_key == key:
                    pending[i] = (key, message)
                    break
            else:
                pending.append((key, message))
        else:
            pending.append((key, message))

        if agent_id not in self._flush_tasks:
            self._flush_tasks[agent_id] = asyncio.create_task(self._flush_after_delay(agent_id))

    async def _flush_after_delay(self, agent_id: UUID) -> None:
        try:
            await asyncio.sleep(self._batch_window_seconds)
            pending = self._pending.pop(agent_id, [])
            if pending:
                await self._deliver_locally(agent_id, [m for _, m in pending])
        finally:
            self._flush_tasks.pop(agent_id, None)

    async def _deliver_locally(self, agent_id: UUID, messages: list[LiveMessage]) -> None:
        connections = self._connections.get(agent_id)
        if not connections:
            return
        dead: list[WebSocket] = []
        for websocket in connections:
            for message in messages:
                payload = message.model_dump(mode="json", by_alias=True)
                try:
                    await websocket.send_json(payload)
                except Exception:
                    dead.append(websocket)
                    break
        for websocket in dead:
            log.info("dropping dead websocket connection", agent_id=str(agent_id))
            connections.discard(websocket)


manager = ConnectionManager()
