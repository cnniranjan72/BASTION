"""Postgres LISTEN/NOTIFY consumer (ARCHITECTURE.md §2.5: "Subscribes to
the event stream... Postgres LISTEN/NOTIFY or a lightweight queue"). LISTEN
requires its own dedicated connection — not one borrowed from the pool,
since notifications are delivered on the specific connection that issued
LISTEN — kept open for the service's lifetime.

Delivery is at-least-once: if this connection drops and reconnects, an
in-flight notification can be lost, but the next event on that trace (or a
future read of GET /traces/{id}, which always falls back to folding
`events` fresh) recovers correctly — the callback re-fetches and re-folds
the *whole* trace on every notification rather than applying an incremental
diff, so processing the same trace_id twice is idempotent by construction.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import asyncpg

from .config import config
from .logging import log

NotificationHandler = Callable[[dict[str, str]], Awaitable[None]]


class EventListener:
    def __init__(self) -> None:
        self._conn: asyncpg.Connection | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def start(self, on_notification: NotificationHandler) -> None:
        self._conn = await asyncpg.connect(config.database_url)
        await self._conn.add_listener("bastion_events", self._enqueue)
        self._task = asyncio.create_task(self._consume(on_notification))

    def _enqueue(self, connection: object, pid: int, channel: str, payload: str) -> None:
        # asyncpg invokes this synchronously from its protocol handling —
        # never do real work here, just hand off and return immediately.
        self._queue.put_nowait(payload)

    async def _consume(self, on_notification: NotificationHandler) -> None:
        while True:
            payload = await self._queue.get()
            try:
                await on_notification(json.loads(payload))
            except Exception:
                log.exception("event listener failed to process notification", payload=payload)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._conn is not None:
            await self._conn.remove_listener("bastion_events", self._enqueue)
            await self._conn.close()
            self._conn = None
