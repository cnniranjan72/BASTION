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

    async def create_agent(
        self, *, org_id: UUID, name: str, api_key_hash: str, policy_set_id: UUID | None
    ) -> asyncpg.Record:
        record = await self.pool.fetchrow(
            """
            INSERT INTO agents (org_id, name, api_key_hash, default_policy_set_id)
            VALUES ($1, $2, $3, $4)
            RETURNING id, org_id, name, default_policy_set_id, created_at
            """,
            org_id,
            name,
            api_key_hash,
            policy_set_id,
        )
        assert record is not None
        return record

    async def list_agents(self, org_id: UUID) -> list[asyncpg.Record]:
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                "SELECT id, org_id, name, default_policy_set_id, created_at FROM agents "
                "WHERE org_id = $1 ORDER BY created_at DESC",
                org_id,
            ),
        )

    async def update_agent_policy_set(
        self, agent_id: UUID, org_id: UUID, policy_set_id: UUID | None
    ) -> asyncpg.Record | None:
        # org_id in the WHERE, not filtered from the response afterward —
        # same check-before-mutate discipline as activate_policy (§ Phase 5
        # bugfix in docs/PROGRESS.md): a cross-org caller can't reassign
        # another org's agent even transiently.
        return await self.pool.fetchrow(
            """
            UPDATE agents SET default_policy_set_id = $1
            WHERE id = $2 AND org_id = $3
            RETURNING id, org_id, name, default_policy_set_id, created_at
            """,
            policy_set_id,
            agent_id,
            org_id,
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

    async def policy_set_belongs_to_org(self, policy_set_id: UUID, org_id: UUID) -> bool:
        """Guards agent creation/update: policy_set_id is a bare UUID in the
        request body, so without this check a caller could assign another
        org's policy_set_id to their own agent — same cross-org-FK class of
        bug activate_policy's org check (docs/PROGRESS.md, Phase 5) exists
        to prevent."""
        row = await self.pool.fetchval(
            "SELECT 1 FROM policy_sets WHERE id = $1 AND org_id = $2", policy_set_id, org_id
        )
        return row is not None

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

    async def activate_policy(self, policy_id: UUID, org_id: UUID) -> asyncpg.Record | None:
        """Deactivates every other version in the same policy_set_id, then
        activates this one, atomically — the partial unique index on
        policies(policy_set_id) WHERE active is the DB-level backstop.

        org_id is checked *before* anything is mutated (not just filtered
        from the response afterward) — a caller from a different org must
        get a no-op, not a real activation they're then merely not shown."""
        async with self.pool.acquire() as conn, conn.transaction():
            target = await conn.fetchrow(
                "SELECT * FROM policies WHERE id = $1 AND org_id = $2", policy_id, org_id
            )
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
        """parent_span_id + agent_id (+ that agent's org_id) from this
        span's CallAttempted event — every span has exactly one. Used to
        authorize GET /approvals/{id} (agent match) and approve/deny (org
        match, Phase 5), and to correctly link ApprovalGranted/
        ApprovalDenied follow-up events into the same causal graph
        position."""
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow(
                """
                SELECT e.parent_span_id, e.agent_id, a.org_id
                FROM events e
                JOIN agents a ON a.id = e.agent_id
                WHERE e.span_id = $1 AND e.event_type = 'CallAttempted'
                """,
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
        self, approval_id: UUID, *, status: str, resolved_by: UUID | None, org_id: UUID
    ) -> asyncpg.Record | None:
        """Only transitions a *pending* request belonging to `org_id` — both
        guards are in the one atomic UPDATE (not a separate check-then-act),
        so double-resolution (two approvers racing) and a cross-org
        approval_id are both a clean no-op, never a real mutation that's
        merely hidden from the response afterward (same reasoning as
        activate_policy's org check)."""
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow(
                """
                UPDATE approval_requests ar
                SET status = $2, resolved_by = $3, resolved_at = now()
                FROM events e
                JOIN agents a ON a.id = e.agent_id
                WHERE ar.id = $1 AND ar.status = 'pending'
                  AND e.span_id = ar.span_id AND e.event_type = 'CallAttempted'
                  AND a.org_id = $4
                RETURNING ar.*
                """,
                approval_id,
                status,
                resolved_by,
                org_id,
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

    # -- Human auth (Phase 5) --------------------------------------------

    async def create_org_and_owner(
        self, *, org_name: str, email: str, password_hash: str
    ) -> asyncpg.Record:
        """Self-serve signup: a brand-new org plus its first user (role
        owner), one transaction. `email` is globally UNIQUE (not scoped to
        org — see migration 0005), so a duplicate raises
        asyncpg.UniqueViolationError; the caller (main.py) translates that
        into a 409, not a raw 500."""
        async with self.pool.acquire() as conn, conn.transaction():
            org_id = await conn.fetchval(
                "INSERT INTO organizations (name) VALUES ($1) RETURNING id", org_name
            )
            record = await conn.fetchrow(
                """
                INSERT INTO users (org_id, email, password_hash, role)
                VALUES ($1, $2, $3, 'owner')
                RETURNING *
                """,
                org_id,
                email,
                password_hash,
            )
            assert record is not None
            return record

    async def get_user_by_email(self, email: str) -> asyncpg.Record | None:
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow("SELECT * FROM users WHERE email = $1", email),
        )

    async def get_user_by_id(self, user_id: UUID) -> asyncpg.Record | None:
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow("SELECT * FROM users WHERE id = $1", user_id),
        )

    async def list_users_for_org(self, org_id: UUID) -> list[asyncpg.Record]:
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                "SELECT id, org_id, email, role, created_at FROM users "
                "WHERE org_id = $1 ORDER BY created_at ASC",
                org_id,
            ),
        )

    async def create_user(
        self, *, org_id: UUID, email: str, password_hash: str, role: str
    ) -> asyncpg.Record:
        record = await self.pool.fetchrow(
            """
            INSERT INTO users (org_id, email, password_hash, role)
            VALUES ($1, $2, $3, $4)
            RETURNING id, org_id, email, role, created_at
            """,
            org_id,
            email,
            password_hash,
            role,
        )
        assert record is not None
        return record

    async def count_owners_for_org(self, org_id: UUID) -> int:
        count = await self.pool.fetchval(
            "SELECT count(*) FROM users WHERE org_id = $1 AND role = 'owner'", org_id
        )
        return cast(int, count)

    async def update_user_role(
        self, user_id: UUID, org_id: UUID, role: str
    ) -> asyncpg.Record | None:
        # Same check-before-mutate discipline as activate_policy/update_agent_policy_set:
        # org_id is in the WHERE, not filtered from the response afterward.
        return await self.pool.fetchrow(
            """
            UPDATE users SET role = $1
            WHERE id = $2 AND org_id = $3
            RETURNING id, org_id, email, role, created_at
            """,
            role,
            user_id,
            org_id,
        )

    async def update_user_password(self, user_id: UUID, password_hash: str) -> None:
        await self.pool.execute(
            "UPDATE users SET password_hash = $1 WHERE id = $2", password_hash, user_id
        )

    # -- API tokens (post-launch) -----------------------------------------

    async def create_api_token(
        self, *, org_id: UUID, user_id: UUID, name: str, token_prefix: str, token_hash: str
    ) -> asyncpg.Record:
        record = await self.pool.fetchrow(
            """
            INSERT INTO api_tokens (org_id, user_id, name, token_prefix, token_hash)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, name, token_prefix, created_at, last_used_at, revoked_at
            """,
            org_id,
            user_id,
            name,
            token_prefix,
            token_hash,
        )
        assert record is not None
        return record

    async def list_api_tokens_for_user(self, user_id: UUID) -> list[asyncpg.Record]:
        # Personal, like a GitHub PAT — scoped to the user who created it,
        # not every teammate in the org (unlike agents/policies, which are
        # org-shared resources).
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                "SELECT id, name, token_prefix, created_at, last_used_at, revoked_at "
                "FROM api_tokens WHERE user_id = $1 ORDER BY created_at DESC",
                user_id,
            ),
        )

    async def get_api_token_by_hash(self, token_hash: str) -> asyncpg.Record | None:
        """Joins users for role/org_id so a valid token authenticates exactly
        like the JWT session of the user who created it — same RBAC, no
        separate/weaker permission model to keep in sync."""
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow(
                """
                SELECT t.id, t.user_id, u.org_id, u.role
                FROM api_tokens t
                JOIN users u ON u.id = t.user_id
                WHERE t.token_hash = $1 AND t.revoked_at IS NULL
                """,
                token_hash,
            ),
        )

    async def touch_api_token(self, token_id: UUID) -> None:
        await self.pool.execute(
            "UPDATE api_tokens SET last_used_at = now() WHERE id = $1", token_id
        )

    async def revoke_api_token(self, token_id: UUID, user_id: UUID) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            """
            UPDATE api_tokens SET revoked_at = now()
            WHERE id = $1 AND user_id = $2 AND revoked_at IS NULL
            RETURNING id, name, token_prefix, created_at, last_used_at, revoked_at
            """,
            token_id,
            user_id,
        )

    # -- Idempotency keys (U2, v2 upgrade) -------------------------------

    async def try_reserve_idempotency_key(
        self,
        *,
        org_id: UUID,
        agent_id: UUID,
        idempotency_key: str,
        trace_id: UUID,
        span_id: UUID,
        parent_span_id: UUID | None,
    ) -> asyncpg.Record | None:
        """Returns the reserved row if this call won the race, None if a
        row for (agent_id, idempotency_key) already exists — the unique
        index is the actual arbiter under concurrency (UPGRADE_ARCHITECTURE.md
        §3), not a check-then-insert race in application code."""
        return await self.pool.fetchrow(
            """
            INSERT INTO idempotency_keys
                (org_id, agent_id, idempotency_key, trace_id, span_id, parent_span_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (agent_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            org_id,
            agent_id,
            idempotency_key,
            trace_id,
            span_id,
            parent_span_id,
        )

    async def get_idempotency_record(
        self, agent_id: UUID, idempotency_key: str
    ) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            "SELECT * FROM idempotency_keys WHERE agent_id = $1 AND idempotency_key = $2",
            agent_id,
            idempotency_key,
        )

    async def complete_idempotency_key(self, row_id: UUID, response_body: dict[str, Any]) -> None:
        await self.pool.execute(
            """
            UPDATE idempotency_keys
            SET status = 'completed', response_body = $1, completed_at = now()
            WHERE id = $2
            """,
            response_body,
            row_id,
        )

    async def insert_refresh_token(
        self, *, user_id: UUID, token_hash: str, family_id: UUID, expires_at: Any
    ) -> asyncpg.Record:
        record = await self.pool.fetchrow(
            """
            INSERT INTO refresh_tokens (user_id, token_hash, family_id, expires_at)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            user_id,
            token_hash,
            family_id,
            expires_at,
        )
        assert record is not None
        return record

    async def get_refresh_token_by_hash(self, token_hash: str) -> asyncpg.Record | None:
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow(
                "SELECT * FROM refresh_tokens WHERE token_hash = $1", token_hash
            ),
        )

    async def revoke_refresh_token(self, token_id: UUID, conn: asyncpg.Connection) -> None:
        await conn.execute("UPDATE refresh_tokens SET revoked_at = now() WHERE id = $1", token_id)

    async def revoke_refresh_token_family(
        self, family_id: UUID, conn: asyncpg.Connection | None = None
    ) -> None:
        """The actual reuse-detection mechanism (AUTH.md §2): revokes every
        token ever issued in this login session, not just the one presented.
        `conn` lets the caller run this inside an existing transaction
        (rotation) or standalone (logout)."""
        executor = conn if conn is not None else self.pool
        await executor.execute(
            "UPDATE refresh_tokens SET revoked_at = now() "
            "WHERE family_id = $1 AND revoked_at IS NULL",
            family_id,
        )

    async def rotate_refresh_token(
        self,
        *,
        old_token_id: UUID,
        user_id: UUID,
        family_id: UUID,
        new_token_hash: str,
        expires_at: Any,
    ) -> asyncpg.Record:
        """Atomically revokes the presented (now-consumed) token and issues
        its replacement in the same family — one-time-use rotation."""
        async with self.pool.acquire() as conn, conn.transaction():
            await self.revoke_refresh_token(old_token_id, conn)
            record = await conn.fetchrow(
                """
                INSERT INTO refresh_tokens (user_id, token_hash, family_id, expires_at)
                VALUES ($1, $2, $3, $4)
                RETURNING *
                """,
                user_id,
                new_token_hash,
                family_id,
                expires_at,
            )
        assert record is not None
        return record


db = Database()
