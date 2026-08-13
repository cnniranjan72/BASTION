"""Live graph-delta fan-out over WebSocket — ARCHITECTURE.md §2.5/§2.6,
BUILD_PLAN.md Phase 6. Connections are grouped by agent_id (matching
`WS /live/{agent_id}` in API_SPEC.md): a viewer only ever sees deltas for
the one agent they subscribed to.

Fan-out is push-based straight from the LISTEN/NOTIFY handler (main.py's
`_handle_notification`) — no polling on either side, which is the actual
Phase 6 milestone ("two browser tabs... no polling").
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from bastion_shared import LiveMessage
from fastapi import WebSocket

from .logging import log


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[UUID, set[WebSocket]] = defaultdict(set)

    async def connect(self, agent_id: UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[agent_id].add(websocket)

    def disconnect(self, agent_id: UUID, websocket: WebSocket) -> None:
        connections = self._connections.get(agent_id)
        if connections is None:
            return
        connections.discard(websocket)
        if not connections:
            self._connections.pop(agent_id, None)

    async def broadcast(self, agent_id: UUID, message: LiveMessage) -> None:
        connections = self._connections.get(agent_id)
        if not connections:
            return
        payload = message.model_dump(mode="json", by_alias=True)
        dead: list[WebSocket] = []
        for websocket in connections:
            try:
                await websocket.send_json(payload)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            log.info("dropping dead websocket connection", agent_id=str(agent_id))
            connections.discard(websocket)


manager = ConnectionManager()
