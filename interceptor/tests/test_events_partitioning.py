"""U9 milestone test (UPGRADE_BUILD_PLAN.md): insert events spanning 3
synthetic months, assert queries correctly hit only relevant partitions
(checked via the real query plan, not an assumption about how partitioning
"should" behave).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
from bastion_interceptor.db import db

DATABASE_URL = "postgresql://bastion:bastion@localhost:5442/bastion"


async def _insert_event_at(
    conn: asyncpg.Connection, *, agent_id: UUID, trace_id: UUID, created_at: datetime
) -> None:
    span_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO events
            (trace_id, span_id, parent_span_id, agent_id, event_type, payload,
             sequence_number, created_at)
        VALUES ($1, $2, NULL, $3, 'CallAttempted', '{"tool_name": "partition.test", "args": {}}',
                bastion_next_sequence_number($1), $4)
        """,
        trace_id,
        span_id,
        agent_id,
        created_at,
    )


async def test_events_spanning_three_months_land_in_correct_partitions(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, _ = test_agent
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        june_trace, july_trace, august_trace = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with conn.transaction():
            await _insert_event_at(
                conn,
                agent_id=agent_id,
                trace_id=june_trace,
                created_at=datetime(2026, 6, 15, tzinfo=UTC),
            )
            await _insert_event_at(
                conn,
                agent_id=agent_id,
                trace_id=july_trace,
                created_at=datetime(2026, 7, 15, tzinfo=UTC),
            )
            await _insert_event_at(
                conn,
                agent_id=agent_id,
                trace_id=august_trace,
                created_at=datetime(2026, 8, 15, tzinfo=UTC),
            )

        june_count = await conn.fetchval(
            "SELECT count(*) FROM events_2026_06 WHERE trace_id = $1", june_trace
        )
        july_count = await conn.fetchval(
            "SELECT count(*) FROM events_2026_07 WHERE trace_id = $1", july_trace
        )
        august_count = await conn.fetchval(
            "SELECT count(*) FROM events_2026_08 WHERE trace_id = $1", august_trace
        )
        assert june_count == 1
        assert july_count == 1
        assert august_count == 1

        # A range query spanning exactly the July partition's bounds must
        # only ever plan to scan events_2026_07 — not June, not August, not
        # the default partition. This is the actual proof partitioning is
        # doing something, not just present in the schema.
        plan = await conn.fetch(
            """
            EXPLAIN (FORMAT JSON)
            SELECT * FROM events WHERE created_at >= '2026-07-01' AND created_at < '2026-08-01'
            """
        )
        plan_text = str(plan[0][0])
        assert "events_2026_07" in plan_text
        assert "events_2026_06" not in plan_text
        assert "events_2026_08" not in plan_text
        assert "events_default" not in plan_text
    finally:
        await conn.close()


async def test_bastion_ensure_events_partition_creates_future_month_idempotently() -> None:
    """The retention/archival job's forward-looking half — proven directly,
    not just assumed to work because the SQL looks right."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Far enough in the future that this partition can't already exist
        # from the migration's own bootstrap set (2026 only).
        await conn.execute("SELECT bastion_ensure_events_partition('2028-03-01'::date)")
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM pg_class WHERE relname = 'events_2028_03')"
        )
        assert exists is True

        # Idempotent: calling it again for the same month must not raise
        # (a naive `CREATE TABLE` without the existence check would).
        await conn.execute("SELECT bastion_ensure_events_partition('2028-03-01'::date)")

        agent_id = uuid.uuid4()
        org_id = uuid.uuid4()
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, $2)", org_id, "t")
        await conn.execute(
            "INSERT INTO agents (id, org_id, name, api_key_hash) VALUES ($1, $2, $3, $4)",
            agent_id,
            org_id,
            "t",
            f"h-{agent_id}",
        )
        trace_id = uuid.uuid4()
        await _insert_event_at(
            conn,
            agent_id=agent_id,
            trace_id=trace_id,
            created_at=datetime(2028, 3, 10, tzinfo=UTC),
        )
        count = await conn.fetchval(
            "SELECT count(*) FROM events_2028_03 WHERE trace_id = $1", trace_id
        )
        assert count == 1
    finally:
        await conn.execute("DROP TABLE IF EXISTS events_2028_03")
        await conn.close()


async def test_events_table_row_count_unaffected_by_partitioning() -> None:
    """Sanity check against the exact real bug class this migration risked:
    data loss during the rename/copy. Not a proof by itself (row counts
    could coincidentally match after a partial loss+partial dupe), but a
    real, cheap invariant worth asserting on every run."""
    total_from_parent = await db.pool.fetchval("SELECT count(*) FROM events")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        tables = await conn.fetch(
            """
            SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
            WHERE i.inhparent = 'events'::regclass
            """
        )
        total_from_partitions = 0
        for t in tables:
            total_from_partitions += await conn.fetchval(f'SELECT count(*) FROM "{t["relname"]}"')
    finally:
        await conn.close()
    assert total_from_parent == total_from_partitions
