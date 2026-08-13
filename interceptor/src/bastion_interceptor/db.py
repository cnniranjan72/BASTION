from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID

import asyncpg
from bastion_shared import EventType

from .config import config


async def _init_connection(conn: asyncpg.Connection) -> None:
    # jsonb <-> Python dict/list automatically, so callers never hand-roll
    # json.dumps/loads around every query that touches a jsonb column.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class Database:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            config.database_url, min_size=1, max_size=10, init=_init_connection
        )

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
                "SELECT id, org_id, name, default_policy_set_id FROM agents "
                "WHERE api_key_hash = $1",
                api_key_hash,
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
        args = (trace_id, span_id, parent_span_id, agent_id, event_type.value, payload)
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
        """The most recent terminal decision event for a span — used to
        recover trace_id/agent_id/parent_span_id for /spans/{id}/complete and
        to confirm the span was actually allowed before letting it complete.
        ApprovalGranted/ApprovalDenied count as CallAllowed/CallBlocked
        equivalents: a span resolved via the approval flow never gets its
        own separate CallAllowed event (Phase 3, see main.py)."""
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow(
                """
                SELECT trace_id, span_id, parent_span_id, agent_id, event_type
                FROM events
                WHERE span_id = $1
                  AND event_type IN
                      ('CallAllowed', 'CallBlocked', 'ApprovalGranted', 'ApprovalDenied')
                ORDER BY sequence_number DESC
                LIMIT 1
                """,
                span_id,
            ),
        )

    # -- Policies (Phase 2) --------------------------------------------

    async def get_active_policies(self) -> list[asyncpg.Record]:
        """Bootstraps the in-memory policy cache at startup."""
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch("SELECT * FROM policies WHERE active"),
        )

    async def get_active_policy_for_set(self, policy_set_id: UUID) -> asyncpg.Record | None:
        """Used to refresh a single cache entry on a hot-reload pub/sub message."""
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow(
                "SELECT * FROM policies WHERE policy_set_id = $1 AND active", policy_set_id
            ),
        )

    async def list_policies(self, org_id: UUID) -> list[asyncpg.Record]:
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                "SELECT * FROM policies WHERE org_id = $1 ORDER BY name, version", org_id
            ),
        )

    async def create_policy(
        self, *, org_id: UUID, name: str, definition: list[dict[str, Any]]
    ) -> asyncpg.Record:
        """Creates a new version. Never mutates an existing row (DATA_MODEL.md:
        "policies are versioned, never edited in place"). Resolves (creating
        if needed) the stable policy_set_id for this (org_id, name) — see
        docs/ARCHITECTURE.md §10 for why that indirection exists."""
        async with self.pool.acquire() as conn, conn.transaction():
            policy_set_id = await conn.fetchval(
                "SELECT id FROM policy_sets WHERE org_id = $1 AND name = $2", org_id, name
            )
            if policy_set_id is None:
                policy_set_id = await conn.fetchval(
                    "INSERT INTO policy_sets (org_id, name) VALUES ($1, $2) RETURNING id",
                    org_id,
                    name,
                )
            next_version = await conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM policies WHERE policy_set_id = $1",
                policy_set_id,
            )
            record = await conn.fetchrow(
                """
                INSERT INTO policies (org_id, policy_set_id, name, version, definition, active)
                VALUES ($1, $2, $3, $4, $5, false)
                RETURNING *
                """,
                org_id,
                policy_set_id,
                name,
                next_version,
                definition,
            )
        assert record is not None
        return record

    async def activate_policy(self, policy_id: UUID) -> asyncpg.Record | None:
        """Deactivates every other version in the same policy_set_id, then
        activates this one, atomically — the partial unique index on
        policies(policy_set_id) WHERE active is the DB-level backstop."""
        async with self.pool.acquire() as conn, conn.transaction():
            target = await conn.fetchrow("SELECT * FROM policies WHERE id = $1", policy_id)
            if target is None:
                return None
            await conn.execute(
                "UPDATE policies SET active = false WHERE policy_set_id = $1",
                target["policy_set_id"],
            )
            record = await conn.fetchrow(
                "UPDATE policies SET active = true WHERE id = $1 RETURNING *", policy_id
            )
        return cast("asyncpg.Record | None", record)

    # -- Approvals (Phase 3) --------------------------------------------

    async def get_span_lineage(self, span_id: UUID) -> asyncpg.Record | None:
        """parent_span_id + agent_id from this span's CallAttempted event —
        every span has exactly one. Used to authorize GET /approvals/{id}
        (agent match) and to correctly link ApprovalGranted/ApprovalDenied
        follow-up events into the same causal graph position."""
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow(
                "SELECT parent_span_id, agent_id FROM events "
                "WHERE span_id = $1 AND event_type = 'CallAttempted'",
                span_id,
            ),
        )

    async def insert_approval_request(self, *, trace_id: UUID, span_id: UUID) -> asyncpg.Record:
        record = await self.pool.fetchrow(
            "INSERT INTO approval_requests (trace_id, span_id) VALUES ($1, $2) RETURNING *",
            trace_id,
            span_id,
        )
        assert record is not None
        return record

    async def get_approval_request(self, approval_id: UUID) -> asyncpg.Record | None:
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow("SELECT * FROM approval_requests WHERE id = $1", approval_id),
        )

    async def expire_stale_approval(
        self, approval_id: UUID, ttl_seconds: float
    ) -> asyncpg.Record | None:
        """Lazily flips a pending approval past its absolute deadline to
        timed_out — checked on each long-poll rather than via a background
        sweeper (see docs/ARCHITECTURE.md's approval-flow section). No-op
        (returns None) if not yet expired or already resolved."""
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow(
                """
                UPDATE approval_requests
                SET status = 'timed_out'
                WHERE id = $1 AND status = 'pending'
                  AND requested_at < now() - make_interval(secs => $2)
                RETURNING *
                """,
                approval_id,
                ttl_seconds,
            ),
        )

    async def resolve_approval(
        self, approval_id: UUID, *, status: str, resolved_by: UUID | None
    ) -> asyncpg.Record | None:
        """Only transitions a *pending* request — the WHERE guard makes
        double-resolution (two approvers racing) a no-op for the loser
        rather than silently overwriting who actually resolved it."""
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow(
                """
                UPDATE approval_requests
                SET status = $2, resolved_by = $3, resolved_at = now()
                WHERE id = $1 AND status = 'pending'
                RETURNING *
                """,
                approval_id,
                status,
                resolved_by,
            ),
        )

    async def list_pending_approvals_for_org(self, org_id: UUID) -> list[asyncpg.Record]:
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                """
                SELECT DISTINCT ar.*
                FROM approval_requests ar
                JOIN events e ON e.span_id = ar.span_id AND e.event_type = 'CallAttempted'
                JOIN agents a ON a.id = e.agent_id
                WHERE a.org_id = $1 AND ar.status = 'pending'
                ORDER BY ar.requested_at
                """,
                org_id,
            ),
        )


db = Database()
