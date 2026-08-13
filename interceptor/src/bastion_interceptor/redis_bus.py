"""Redis pub/sub hot-reload propagation (ARCHITECTURE.md §2.3): a policy
change published by one interceptor instance (or a future dedicated
dashboard-API service) is picked up by every subscribed interceptor
instance, updating its in-memory PolicyCache with no restart."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from uuid import UUID

import redis.asyncio as redis

from .config import config
from .logging import log

POLICY_UPDATES_CHANNEL = "bastion:policy_updates"


class RedisBus:
    def __init__(self) -> None:
        self._redis: redis.Redis | None = None
        self._pubsub: redis.client.PubSub | None = None
        self._listener_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        # protocol=2 (RESP2) deliberately: the RESP3 HELLO handshake this
        # client defaults to fails against the local Redis in this
        # environment (observed: "unknown command 'HELLO'" even though the
        # server supports HELLO fine via redis-cli directly — a client/async
        # I/O quirk, not a real server capability gap). Plain pub/sub never
        # needed RESP3 features, so pinning RESP2 sidesteps it entirely.
        self._redis = redis.from_url(config.redis_url, decode_responses=True, protocol=2)

    async def close(self) -> None:
        if self._listener_task is not None:
            self._listener_task.cancel()
            self._listener_task = None
        if self._pubsub is not None:
            await self._pubsub.aclose()  # type: ignore[no-untyped-call]
            self._pubsub = None
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def publish_policy_update(self, policy_set_id: UUID) -> None:
        if self._redis is None:
            raise RuntimeError("RedisBus.connect() was not called")
        await self._redis.publish(
            POLICY_UPDATES_CHANNEL, json.dumps({"policy_set_id": str(policy_set_id)})
        )

    async def start_policy_listener(self, on_update: Callable[[UUID], Awaitable[None]]) -> None:
        if self._redis is None:
            raise RuntimeError("RedisBus.connect() was not called")
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(POLICY_UPDATES_CHANNEL)
        self._listener_task = asyncio.create_task(self._listen(on_update))

    async def _listen(self, on_update: Callable[[UUID], Awaitable[None]]) -> None:
        assert self._pubsub is not None
        async for message in self._pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
                await on_update(UUID(data["policy_set_id"]))
            except Exception:
                log.exception("policy hot-reload listener failed to process message")


redis_bus = RedisBus()
