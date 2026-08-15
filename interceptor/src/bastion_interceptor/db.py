from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

import asyncpg
from bastion_shared import EventType

from . import object_storage, tracing
from .config import config


async def _init_connection(conn: asyncpg.Connection) -> None:
    # jsonb <-> Python dict/list automatically, so callers never hand-roll
    # json.dumps/loads around every query that touches a jsonb column.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class PolicyVersionConflict(Exception):
    """U4 (v2 upgrade), ADR-016: raised by create_policy when based_on_version
    no longer matches the actual current version — main.py maps this to 409."""

    def __init__(self, *, policy_set_id: UUID, current_version: int | None) -> None:
        self.policy_set_id = policy_set_id
        self.current_version = current_version
        super().__init__(
            f"policy_set {policy_set_id} has moved past the caller's based_on_version "
            f"(current_version={current_version})"
        )


class Database:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None
        self._app_pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        # U9 (v2 upgrade), UPGRADE_ARCHITECTURE.md §11: command_timeout
        # bounds every individual query on this pool — a hung/runaway query
        # can no longer hold a connection (and, transitively, exhaust the
        # pool under load) forever.
        self._pool = await asyncpg.create_pool(
            config.database_url,
            min_size=1,
            max_size=10,
            init=_init_connection,
            command_timeout=config.db_query_timeout_seconds,
        )
        # U8 (v2 upgrade): a separate pool, connected as the non-superuser
        # `bastion_app` role — see org_scoped_connection below and ADR-009
        # for why RLS needs this rather than just adding policies to the
        # pool above.
        self._app_pool = await asyncpg.create_pool(
            config.app_database_url,
            min_size=1,
            max_size=5,
            init=_init_connection,
            command_timeout=config.db_query_timeout_seconds,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
        if self._app_pool is not None:
            await self._app_pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database.connect() was not called")
        return self._pool

    @asynccontextmanager
    async def org_scoped_connection(self, org_id: UUID) -> AsyncIterator[asyncpg.Connection]:
        """U8 (v2 upgrade), ADR-009: acquires a connection from the
        restricted `bastion_app` pool and sets Postgres Row-Level
        Security's session context (`app.current_org_id`) for the duration
        of one transaction. Every RLS-enabled table (organizations, agents,
        policy_sets, policies, trace_summaries, users, api_tokens,
        idempotency_keys — see migration 0010) is filtered to exactly this
        org_id by Postgres itself, enforced even if the caller's own query
        has no `WHERE org_id = ...` clause at all — real defense-in-depth
        against the application-layer scoping CLAUDE.md rule #7 already
        requires, not a replacement for it.

        Scope, stated explicitly rather than silently assumed complete: not
        every call site in this file has been retrofitted to use this —
        the ones that have are noted at their definitions. The rest
        continue to rely solely on application-layer `WHERE org_id`
        scoping, exactly as before this phase."""
        assert self._app_pool is not None, "Database.connect() was not called"
        async with self._app_pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org_id))
            yield conn

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
        """U8 (v2 upgrade): scoped via org_scoped_connection/RLS (migration
        0010) instead of an application-layer `WHERE org_id` filter —
        deliberately, as the concrete demonstration this phase's milestone
        test wants: Postgres itself guarantees this can never return
        another org's agents, even if this query were accidentally
        rewritten without any org-scoping clause at all."""
        async with self.org_scoped_connection(org_id) as conn:
            return cast(
                list[asyncpg.Record],
                await conn.fetch(
                    "SELECT id, org_id, name, default_policy_set_id, created_at FROM agents "
                    "ORDER BY created_at DESC"
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
        """Assigns sequence_number and inserts the event, plus (U3, v2
        upgrade) an outbox_events row, in one transaction —
        UPGRADE_ARCHITECTURE.md §4.1's transactional outbox: if the process
        crashes between the two, there is no "between", both committed or
        neither did. `bastion_next_sequence_number()` takes a
        transaction-scoped advisory lock keyed on trace_id, so concurrent
        inserts for the *same* trace serialize (strictly increasing, no
        gaps, no duplicates) while inserts for *different* traces never
        block each other.

        U9 (v2 upgrade): `payload` is run through `object_storage.upload_if_large`
        first — under the threshold, it passes through unchanged; at or
        above it, both this row's and the outbox row's payload become the
        same small pointer object, never the large payload itself. This
        keeps `events`/`outbox_events` rows bounded in size regardless of
        how large a tool's actual response was.

        U12 (v2 upgrade): also captures the currently-active OTel trace
        context (tracing.capture_trace_context, present only inside a real
        request — a no-op empty dict otherwise, e.g. in a test/script
        context with no tracer configured) onto the outbox row, for the
        outbox publisher to later turn into Kafka headers — see
        tracing.py's module docstring for why this can't just be injected
        directly at publish time.
        """
        payload = await object_storage.upload_if_large(payload)
        otel_trace_context = tracing.capture_trace_context()
        events_query = """
            INSERT INTO events
                (trace_id, span_id, parent_span_id, agent_id, event_type, payload, sequence_number)
            VALUES
                ($1, $2, $3, $4, $5, $6, bastion_next_sequence_number($1))
            RETURNING event_id
        """
        outbox_query = """
            INSERT INTO outbox_events
                (event_id, trace_id, span_id, parent_span_id, agent_id, event_type, payload,
                 otel_trace_context)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """
        args = (trace_id, span_id, parent_span_id, agent_id, event_type.value, payload)

        async def _write(executor: asyncpg.Connection | asyncpg.Pool) -> None:
            event_id = await executor.fetchval(events_query, *args)
            await executor.execute(
                outbox_query,
                event_id,
                trace_id,
                span_id,
                parent_span_id,
                agent_id,
                event_type.value,
                payload,
                otel_trace_context,
            )

        if conn is not None:
            await _write(conn)
        else:
            async with self.pool.acquire() as acquired, acquired.transaction():
                await _write(acquired)

    async def get_unpublished_outbox_events(self, limit: int) -> list[asyncpg.Record]:
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                "SELECT * FROM outbox_events WHERE published_at IS NULL ORDER BY id LIMIT $1",
                limit,
            ),
        )

    async def get_outbox_events_for_trace(self, trace_id: UUID) -> list[asyncpg.Record]:
        """Test-only helper (test_outbox_resumability.py): scoping by
        trace_id keeps resumability assertions correct regardless of
        whatever unrelated backlog other tests may have left in the shared
        dev database — `get_unpublished_outbox_events` is intentionally
        global (that's what the real publisher needs), which makes it the
        wrong tool for a single test to reason about its own rows in
        isolation."""
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                "SELECT * FROM outbox_events WHERE trace_id = $1 ORDER BY id", trace_id
            ),
        )

    async def mark_outbox_events_published(self, ids: list[int]) -> None:
        if not ids:
            return
        await self.pool.execute(
            "UPDATE outbox_events SET published_at = now() WHERE id = ANY($1::bigint[])", ids
        )

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

    async def get_call_attempted_payload(self, span_id: UUID) -> dict[str, Any] | None:
        """This span's original CallAttemptedPayload (`{tool_name, args}`) —
        used both by the circuit breaker (U6, keyed on tool_name, which
        isn't on get_span_decision's terminal-decision-event columns) and
        by the authorization chain (U7, needs the underlying call's `args`
        — e.g. `amount` — to evaluate an approval action against).

        U9 (v2 upgrade): resolved through object_storage.resolve_payload —
        a caller here always gets the real payload back, transparently,
        whether it was stored inline or offloaded. This is (deliberately,
        see object_storage.py's module docstring) the one payload-reading
        call site retrofitted this phase; get_events_for_trace's rows stay
        raw Records (payload is one of several fields there, not cleanly
        wrappable without a larger interface change) and are not resolved."""
        raw = cast(
            "dict[str, Any] | None",
            await self.pool.fetchval(
                """
                SELECT payload FROM events
                WHERE span_id = $1 AND event_type = 'CallAttempted'
                LIMIT 1
                """,
                span_id,
            ),
        )
        if raw is None:
            return None
        return await object_storage.resolve_payload(raw)

    async def get_span_tool_name(self, span_id: UUID) -> str | None:
        payload = await self.get_call_attempted_payload(span_id)
        return payload["tool_name"] if payload is not None else None

    # -- Policies (Phase 2) --------------------------------------------

    async def get_policy_set_id_by_name(self, org_id: UUID, name: str) -> UUID | None:
        """U7 (v2 upgrade): looks up a policy_set by its stable (org_id,
        name) identity directly — authorization.py uses this to find an
        org's authorization policy set by its reserved well-known name,
        the same lookup create_policy already does internally when
        resolving which set a new version belongs to."""
        return cast(
            "UUID | None",
            await self.pool.fetchval(
                "SELECT id FROM policy_sets WHERE org_id = $1 AND name = $2", org_id, name
            ),
        )

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
        self,
        *,
        org_id: UUID,
        name: str,
        definition: list[dict[str, Any]],
        based_on_version: int | None = None,
    ) -> asyncpg.Record:
        """Creates a new version. Never mutates an existing row (DATA_MODEL.md:
        "policies are versioned, never edited in place"). Resolves (creating
        if needed) the stable policy_set_id for this (org_id, name) — see
        docs/ARCHITECTURE.md §10 for why that indirection exists.

        U4 (v2 upgrade), optimistic concurrency (ADR-016): UPGRADE_ARCHITECTURE.md
        §5 drafts this as an in-place `UPDATE ... WHERE version = $3`, which
        assumes a mutable row this table deliberately isn't — the append-only
        design is intentional (test_create_policy_does_not_mutate_previous_version)
        and predates this phase. The equivalent guarantee for an immutable,
        versioned-row model: `based_on_version`, if supplied, must still match
        the actual current latest version at insert time, or this raises
        `PolicyVersionConflict` instead of silently creating a version past
        one a concurrent editor already committed. Two concurrent callers who
        both pass the same (now-stale) `based_on_version` race on the
        `UNIQUE (policy_set_id, version)` constraint itself — the real
        arbiter, same DB-constraint-not-app-level-check pattern as ADR-005 —
        the loser's `UniqueViolationError` is caught below and converted the
        same way. `based_on_version=None` preserves v1's original behavior
        exactly: blind append, no conflict detection, for backward
        compatibility with callers that predate this field.
        """
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
            current_version = await conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) FROM policies WHERE policy_set_id = $1",
                policy_set_id,
            )
            if based_on_version is not None and based_on_version != current_version:
                raise PolicyVersionConflict(
                    policy_set_id=policy_set_id, current_version=current_version
                )
            next_version = current_version + 1
            try:
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
            except asyncpg.exceptions.UniqueViolationError as exc:
                # A second caller committed `next_version` first, between our
                # read above and this INSERT — the exact race the read-then-
                # check above narrows but can't fully close on its own.
                raise PolicyVersionConflict(
                    policy_set_id=policy_set_id, current_version=None
                ) from exc
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
        self, approval_id: UUID, ttl_seconds: float, conn: asyncpg.Connection | None = None
    ) -> asyncpg.Record | None:
        """Lazily flips a pending approval past its absolute deadline to
        timed_out — checked on each long-poll rather than via a background
        sweeper (see docs/ARCHITECTURE.md's approval-flow section). No-op
        (returns None) if not yet expired or already resolved.

        `conn`, same reasoning as resolve_approval: pass the transaction
        this write shares with the following ApprovalDenied event insert,
        so no external reader can observe status='timed_out' before that
        event exists."""
        executor = conn if conn is not None else self.pool
        return cast(
            "asyncpg.Record | None",
            await executor.fetchrow(
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
        self,
        approval_id: UUID,
        *,
        status: str,
        resolved_by: UUID | None,
        org_id: UUID,
        conn: asyncpg.Connection | None = None,
    ) -> asyncpg.Record | None:
        """Only transitions a *pending* request belonging to `org_id` — both
        guards are in the one atomic UPDATE (not a separate check-then-act),
        so double-resolution (two approvers racing) and a cross-org
        approval_id are both a clean no-op, never a real mutation that's
        merely hidden from the response afterward (same reasoning as
        activate_policy's org check).

        `conn`, if passed, must be the same transaction main.py's
        `_resolve_approval` uses for the immediately-following
        ApprovalGranted/ApprovalDenied event insert — see that function's
        docstring for the real race this closes: without one shared
        transaction, a concurrent GET /approvals/{id} poller can observe
        `status = 'approved'` (this write, committed) before the event
        exists (the next write, not yet committed), race ahead to
        POST /spans/{id}/complete, and get a spurious 404 there."""
        executor = conn if conn is not None else self.pool
        return cast(
            "asyncpg.Record | None",
            await executor.fetchrow(
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
