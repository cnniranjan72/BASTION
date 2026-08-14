from __future__ import annotations

import json
from typing import cast
from uuid import UUID

import asyncpg
from bastion_shared import TraceGraph

from .config import config


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class Database:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            config.database_url,
            min_size=1,
            max_size=10,
            init=_init_connection,
            command_timeout=config.db_query_timeout_seconds,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database.connect() was not called")
        return self._pool

    async def get_events_for_trace(self, trace_id: UUID) -> list[asyncpg.Record]:
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                "SELECT * FROM events WHERE trace_id = $1 ORDER BY sequence_number ASC", trace_id
            ),
        )

    async def get_trace_summary(self, trace_id: UUID) -> asyncpg.Record | None:
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow("SELECT * FROM trace_summaries WHERE trace_id = $1", trace_id),
        )

    async def list_trace_summaries(self, org_id: UUID, limit: int = 50) -> list[asyncpg.Record]:
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                """
                SELECT * FROM trace_summaries
                WHERE org_id = $1
                ORDER BY started_at DESC
                LIMIT $2
                """,
                org_id,
                limit,
            ),
        )

    async def upsert_trace_summary(self, *, org_id: UUID, graph: TraceGraph) -> None:
        """A projection rebuildable from `events`, per DATA_MODEL.md: if this
        table and `events` ever disagree, `events` wins. Upsert because a
        trace can only reach a terminal state once, but re-processing the
        same notification twice (at-least-once delivery, see
        docs/ARCHITECTURE.md) must stay idempotent.

        Individual columns use native Python values (UUID/datetime objects,
        what asyncpg's non-jsonb codecs expect); graph_snapshot uses the
        JSON-mode dump (what the jsonb codec's json.dumps encoder expects) —
        same object, two different dumps for two different destinations.
        """
        await self.pool.execute(
            """
            INSERT INTO trace_summaries
                (trace_id, agent_id, org_id, status, total_cost, total_calls,
                 blocked_calls, started_at, ended_at, graph_snapshot)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (trace_id) DO UPDATE SET
                status = EXCLUDED.status,
                total_cost = EXCLUDED.total_cost,
                total_calls = EXCLUDED.total_calls,
                blocked_calls = EXCLUDED.blocked_calls,
                ended_at = EXCLUDED.ended_at,
                graph_snapshot = EXCLUDED.graph_snapshot
            """,
            graph.trace_id,
            graph.agent_id,
            org_id,
            graph.status,
            graph.total_cost,
            graph.total_calls,
            graph.blocked_calls,
            graph.started_at,
            graph.ended_at,
            graph.model_dump(mode="json"),
        )

    async def get_org_id_for_agent(self, agent_id: UUID) -> UUID | None:
        return cast(
            "UUID | None",
            await self.pool.fetchval("SELECT org_id FROM agents WHERE id = $1", agent_id),
        )


db = Database()
