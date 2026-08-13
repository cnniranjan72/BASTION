from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID

import asyncpg
from bastion_shared import EventType

from .config import config


class Database:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(config.database_url, min_size=1, max_size=10)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database.connect() was not called")
        return self._pool

    async def get_agent_by_api_key_hash(self, api_key_hash: str) -> asyncpg.Record | None:
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow(
                "SELECT id, org_id, name FROM agents WHERE api_key_hash = $1", api_key_hash
            ),
        )

    async def insert_event(
        self,
        *,
        trace_id: UUID,
        span_id: UUID,
        parent_span_id: UUID | None,
        agent_id: UUID,
        event_type: EventType,
        payload: dict[str, Any],
        conn: asyncpg.Connection | None = None,
    ) -> None:
        """Assigns sequence_number and inserts the event in one transaction.

        bastion_next_sequence_number() takes a transaction-scoped advisory
        lock keyed on trace_id, so concurrent inserts for the *same* trace
        serialize (strictly increasing, no gaps, no duplicates) while
        inserts for *different* traces never block each other.
        """
        query = """
            INSERT INTO events
                (trace_id, span_id, parent_span_id, agent_id, event_type, payload, sequence_number)
            VALUES
                ($1, $2, $3, $4, $5, $6, bastion_next_sequence_number($1))
        """
        args = (
            trace_id,
            span_id,
            parent_span_id,
            agent_id,
            event_type.value,
            json.dumps(payload),
        )
        executor = conn if conn is not None else self.pool
        await executor.execute(query, *args)

    async def get_events_for_trace(self, trace_id: UUID) -> list[asyncpg.Record]:
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                "SELECT * FROM events WHERE trace_id = $1 ORDER BY sequence_number ASC", trace_id
            ),
        )

    async def get_span_decision(self, span_id: UUID) -> asyncpg.Record | None:
        """The most recent CallAllowed/CallBlocked event for a span — used to
        recover trace_id/agent_id/parent_span_id for /spans/{id}/complete and
        to confirm the span was actually allowed before letting it complete.
        """
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow(
                """
                SELECT trace_id, span_id, parent_span_id, agent_id, event_type
                FROM events
                WHERE span_id = $1 AND event_type IN ('CallAllowed', 'CallBlocked')
                ORDER BY sequence_number DESC
                LIMIT 1
                """,
                span_id,
            ),
        )


db = Database()
