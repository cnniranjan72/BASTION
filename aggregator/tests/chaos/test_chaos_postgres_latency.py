"""U14 chaos scenario (UPGRADE_ARCHITECTURE.md §16): "Postgres +500ms
latency injected" — required invariant: "interceptor p99 degrades
predictably, does not deadlock or cascade-fail."

No network-level fault-injection tool exists in this stack (UPGRADE_ARCHITECTURE.md
§18's stack list deliberately doesn't add one — "no new technology gets
added beyond this list unless it's solving a demonstrated problem"), and
one isn't demonstrably needed here: `interceptor_db._pool` (the exact same
asyncpg pool object `/intercept`'s request handlers use) is swapped for a
thin proxy for the duration of this test only, then restored — real
finding while building this: `asyncpg.Pool` defines `__slots__`, so its
`fetch`/`fetchrow`/etc. can't be monkeypatched on the live instance
directly (`AttributeError: 'Pool' object attribute 'fetch' is read-only`,
confirmed by running this exact approach first). Swapping the `Database`
instance's own `_pool` attribute (a plain attribute, not slotted) for a
proxy sidesteps that entirely. The proxy also has to wrap `pool.acquire()`
specifically, not just the pool's own verb methods: `insert_event` (the
hot-path write every `/intercept` call makes) acquires a connection and
calls `fetchval`/`execute` on *that connection*, not on the pool object —
missing this would have silently under-delayed the exact write this test
most wants to slow down.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any
from uuid import UUID

import asyncpg
import httpx
from bastion_interceptor.db import db as interceptor_db
from bastion_interceptor.main import app as interceptor_app

INJECTED_DELAY_SECONDS = 0.5
CONCURRENCY = 5


class _DelayedConnProxy:
    def __init__(self, conn: asyncpg.Connection, delay: float) -> None:
        self._conn = conn
        self._delay = delay

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    async def fetch(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(self._delay)
        return await self._conn.fetch(*args, **kwargs)

    async def fetchrow(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(self._delay)
        return await self._conn.fetchrow(*args, **kwargs)

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(self._delay)
        return await self._conn.fetchval(*args, **kwargs)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(self._delay)
        return await self._conn.execute(*args, **kwargs)


class _DelayedAcquireContext:
    def __init__(self, real_context: Any, delay: float) -> None:
        self._real_context = real_context
        self._delay = delay

    async def __aenter__(self) -> _DelayedConnProxy:
        conn = await self._real_context.__aenter__()
        return _DelayedConnProxy(conn, self._delay)

    async def __aexit__(self, *exc_info: Any) -> Any:
        return await self._real_context.__aexit__(*exc_info)


class _DelayedPoolProxy:
    """Stands in for `interceptor_db._pool` — real asyncpg.Pool underneath,
    every read/write path genuinely slower, nothing fabricated."""

    def __init__(self, pool: asyncpg.Pool, delay: float) -> None:
        self._pool = pool
        self._delay = delay

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pool, name)

    async def fetch(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(self._delay)
        return await self._pool.fetch(*args, **kwargs)

    async def fetchrow(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(self._delay)
        return await self._pool.fetchrow(*args, **kwargs)

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(self._delay)
        return await self._pool.fetchval(*args, **kwargs)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(self._delay)
        return await self._pool.execute(*args, **kwargs)

    def acquire(self, *args: Any, **kwargs: Any) -> _DelayedAcquireContext:
        return _DelayedAcquireContext(self._pool.acquire(*args, **kwargs), self._delay)


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=interceptor_app), base_url="http://interceptor.test"
    )


async def _timed_intercept(agent_id: UUID, raw_key: str) -> tuple[int, float]:
    body = {
        "trace_id": str(uuid.uuid4()),
        "parent_span_id": None,
        "tool_name": "chaos.pg_latency_test",
        "args": {},
        "agent_id": str(agent_id),
        "idempotency_key": str(uuid.uuid4()),
    }
    headers = {"Authorization": f"Bearer {raw_key}"}
    start = time.monotonic()
    async with _http_client() as http:
        response = await http.post("/intercept", json=body, headers=headers)
    elapsed = time.monotonic() - start
    return response.status_code, elapsed


async def test_intercept_degrades_predictably_without_deadlock_under_slow_postgres(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, raw_key = test_agent
    real_pool = interceptor_db.pool
    interceptor_db._pool = _DelayedPoolProxy(real_pool, INJECTED_DELAY_SECONDS)  # type: ignore[assignment]

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(_timed_intercept(agent_id, raw_key) for _ in range(CONCURRENCY))),
            # Generous upper bound proving "does not deadlock": a genuine
            # deadlock/cascade failure would never resolve at all, so any
            # finite bound here is really testing "resolves at all," not
            # tuning a tight SLO — U13's own load-testing SLO work already
            # owns the tight-latency-bound question.
            timeout=30.0,
        )
    finally:
        interceptor_db._pool = real_pool

    statuses = [status for status, _ in results]
    latencies = [elapsed for _, elapsed in results]
    assert all(status == 200 for status in statuses), (
        f"expected every request to still succeed under injected latency, got {statuses}"
    )
    # Predictable degradation: every request does at least one real DB
    # round trip (get_agent_by_api_key_hash for auth, insert_event for the
    # decision), so each latency must be at least the injected delay —
    # never near-zero (would mean the delay silently didn't apply) and
    # never wildly beyond a handful of sequential round trips' worth (would
    # indicate cascading queueing rather than bounded, predictable cost).
    assert all(elapsed >= INJECTED_DELAY_SECONDS for elapsed in latencies), latencies
    assert all(elapsed < INJECTED_DELAY_SECONDS * 10 for elapsed in latencies), latencies

    # Reversibility: once Postgres is fast again, latency drops back down
    # immediately — no lingering pool corruption from the injected delay.
    status, elapsed = await _timed_intercept(agent_id, raw_key)
    assert status == 200
    assert elapsed < INJECTED_DELAY_SECONDS, (
        f"expected latency to recover once the injected delay is removed, got {elapsed:.3f}s"
    )
