"""U8 milestone test (UPGRADE_BUILD_PLAN.md): with RLS enabled, attempt a
cross-org read using org A's session context targeting org B's data — assert
it returns nothing, even if the application-layer `WHERE org_id` filter is
deliberately removed in the test, to prove the DB layer alone enforces
isolation.

Connects directly via `db.org_scoped_connection` (interceptor/db.py, U8) —
the same mechanism `list_agents` now uses in production — with queries that
have no `WHERE org_id` clause at all, so a pass here can only mean Postgres's
Row-Level Security (migration 0010) is doing the isolating, not application
code.
"""

from __future__ import annotations

import os
import uuid
from uuid import UUID

import asyncpg
import pytest
from bastion_interceptor.db import db

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://bastion:bastion@localhost:5442/bastion")


async def _make_org_with_agent() -> tuple[UUID, UUID]:
    """Raw SQL via the superuser connection — setup only, not the thing
    under test."""
    org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name) VALUES ($1, $2)", org_id, f"rls-test-{org_id}"
        )
        await conn.execute(
            "INSERT INTO agents (id, org_id, name, api_key_hash) VALUES ($1, $2, $3, $4)",
            agent_id,
            org_id,
            "rls-test-agent",
            f"rls-test-hash-{agent_id}",
        )
    finally:
        await conn.close()
    return org_id, agent_id


async def test_rls_blocks_cross_org_read_with_no_application_filter() -> None:
    org_a, agent_a = await _make_org_with_agent()
    org_b, agent_b = await _make_org_with_agent()

    async with db.org_scoped_connection(org_a) as conn:
        # Deliberately no WHERE org_id here — the whole point is proving
        # Postgres restricts this on its own.
        rows = await conn.fetch("SELECT id FROM agents")
    visible_ids = {r["id"] for r in rows}

    assert agent_a in visible_ids
    assert agent_b not in visible_ids


async def test_rls_session_with_no_org_context_sees_nothing() -> None:
    """Fail-closed, not fail-open: a bastion_app connection that never
    calls set_config('app.current_org_id', ...) must see zero rows on an
    RLS-enabled table, not everything — the safe default for a mechanism
    whose entire purpose is catching an *omitted* scoping step."""
    await _make_org_with_agent()  # at least one real row exists in the table

    assert db._app_pool is not None
    async with db._app_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id FROM agents")
    assert rows == []


async def test_superuser_connection_is_unaffected_by_rls() -> None:
    """Documents, rather than merely assumes, the exact reason
    org_scoped_connection exists as a *separate* pool (ADR-009): the
    superuser role every other connection in this system uses
    unconditionally bypasses RLS, no policy can change that — so this
    query, over the ordinary `db.pool`, must see rows from multiple orgs
    at once despite the same policies being present on the table."""
    org_a, agent_a = await _make_org_with_agent()
    org_b, agent_b = await _make_org_with_agent()

    rows = await db.pool.fetch(
        "SELECT id FROM agents WHERE id = ANY($1::uuid[])", [agent_a, agent_b]
    )
    visible_ids = {r["id"] for r in rows}
    assert agent_a in visible_ids
    assert agent_b in visible_ids


async def test_list_agents_isolated_by_rls_not_application_filter() -> None:
    """The real, already-shipped call site (db.list_agents, used by
    GET /agents) — end-to-end proof the retrofit actually works, not just
    the standalone mechanism."""
    org_a, agent_a = await _make_org_with_agent()
    org_b, agent_b = await _make_org_with_agent()

    org_a_agents = await db.list_agents(org_a)
    ids = {r["id"] for r in org_a_agents}
    assert agent_a in ids
    assert agent_b not in ids


@pytest.mark.parametrize(
    "table",
    [
        "organizations",
        "agents",
        "policy_sets",
        "policies",
        "trace_summaries",
        "users",
        "api_tokens",
        "idempotency_keys",
    ],
)
async def test_every_scoped_table_has_row_level_security_enabled(table: str) -> None:
    """Migration 0010 covers a specific, named list of tables — this
    guards against a future migration accidentally dropping RLS from one
    of them (e.g. via a careless `ALTER TABLE ... DISABLE ROW LEVEL
    SECURITY` or a table recreation) without anyone noticing."""
    row = await db.pool.fetchrow(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = $1", table
    )
    assert row is not None, f"table {table} not found"
    assert row["relrowsecurity"] is True, f"{table} does not have RLS enabled"
    assert row["relforcerowsecurity"] is True, f"{table} does not have FORCE RLS"
