"""Redis pub/sub for live WS fan-out across gateway instances — U11 (v2
upgrade), UPGRADE_ARCHITECTURE.md §13. Fixes v1's actual scaling gap: a
single-process `ConnectionManager` (ws.py) only ever reaches clients
connected to *that* process — the moment there's a second WS gateway
instance, client A on gateway 1 and client B on gateway 2 have no way to
learn about each other's events. Every gateway instance publishes and
subscribes to the same per-agent channel, so any instance can deliver to
any client regardless of which instance actually processed the underlying
Kafka message.

RESP2 pinned, same reasoning as interceptor/redis_bus.py: the RESP3 HELLO
handshake this client defaults to fails against the local Redis in this
environment.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from uuid import UUID

import redis.asyncio as redis
from bastion_shared import LiveMessage
from pydantic import TypeAdapter

from .config import config

_live_message_adapter: TypeAdapter[LiveMessage] = TypeAdapter(LiveMessage)


def _channel(agent_id: UUID) -> str:
    return f"bastion:ws:{agent_id}"


class RedisBus:
    def __init__(self) -> None:
        self._redis: redis.Redis | None = None

    async def connect(self) -> None:
        self._redis = redis.from_url(config.redis_url, decode_responses=True, protocol=2)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def publish_live_message(self, agent_id: UUID, message: LiveMessage) -> None:
        if self._redis is None:
            raise RuntimeError("RedisBus.connect() was not called")
        payload = message.model_dump(mode="json", by_alias=True)
        await self._redis.publish(_channel(agent_id), json.dumps(payload))

    async def agents_with_open_circuit_breaker(self) -> set[UUID]:
        """U16 (v2 upgrade), Command Center's "agents healthy" count
        (FRONTEND_V2.md, ADR-021) -- real state, not synthetic: reads the
        same `bastion:breaker:{agent_id}:{tool_name}:state` keys
        interceptor/circuit_breaker.py writes. Aggregator has no other
        access to this (it's Redis-only, never persisted to Postgres), so
        this reads the shared Redis directly rather than adding a new
        cross-service API call for one gauge."""
        if self._redis is None:
            raise RuntimeError("RedisBus.connect() was not called")
        open_agents: set[UUID] = set()
        async for key in self._redis.scan_iter(match="bastion:breaker:*:state"):
            value = await self._redis.get(key)
            if value == "OPEN":
                # bastion:breaker:{agent_id}:{tool_name}:state -- agent_id is
                # always the segment right after the fixed "bastion:breaker:"
                # prefix, regardless of whether tool_name itself contains ":".
                open_agents.add(UUID(key.split(":")[2]))
        return open_agents

    async def subscribe_live_messages(self, agent_id: UUID) -> AsyncIterator[LiveMessage]:
        """Runs until cancelled (the caller's own task, per-agent, ws.py's
        ConnectionManager) — cleans up its subscription on the way out
        either way (normal generator close or task cancellation)."""
        if self._redis is None:
            raise RuntimeError("RedisBus.connect() was not called")
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(_channel(agent_id))
        try:
            async for raw in pubsub.listen():
                if raw["type"] != "message":
                    continue
                yield _live_message_adapter.validate_json(raw["data"])
        finally:
            await pubsub.unsubscribe(_channel(agent_id))
            await pubsub.aclose()  # type: ignore[no-untyped-call]


redis_bus = RedisBus()
