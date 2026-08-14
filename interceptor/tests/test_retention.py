"""U9 retention/archival tests — UPGRADE_ARCHITECTURE.md §11: "hot events
live in Postgres partitions; older partitions get archived to object
storage and detached." Uses synthetic old partitions (2020/2021, far past
the 90-day retention window from any realistic "today"), never touching
real accumulated dev data in the current-year partitions.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from bastion_interceptor import object_storage, retention
from bastion_interceptor.config import config
from bastion_interceptor.db import db


async def _insert_synthetic_old_event(agent_id: UUID, trace_id: UUID, created_at: datetime) -> None:
    span_id = uuid.uuid4()
    await db.pool.execute(
        """
        INSERT INTO events
            (trace_id, span_id, parent_span_id, agent_id, event_type, payload,
             sequence_number, created_at)
        VALUES ($1, $2, NULL, $3, 'CallAttempted', '{"tool_name": "retention.test", "args": {}}',
                bastion_next_sequence_number($1), $4)
        """,
        trace_id,
        span_id,
        agent_id,
        created_at,
    )


async def test_archive_and_detach_partition_round_trips_through_object_storage(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, _ = test_agent
    await db.pool.execute("SELECT bastion_ensure_events_partition('2020-01-01'::date)")

    trace_ids = [uuid.uuid4() for _ in range(3)]
    for trace_id in trace_ids:
        await _insert_synthetic_old_event(agent_id, trace_id, datetime(2020, 1, 15, tzinfo=UTC))

    eligible = await retention.list_partitions_older_than(retention_days=90)
    assert "events_2020_01" in eligible

    archived_count = await retention.archive_and_detach_partition("events_2020_01")
    assert archived_count == 3

    # Detached: no longer a partition of events at all.
    still_attached = await db.pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
            WHERE i.inhparent = 'events'::regclass AND c.relname = 'events_2020_01'
        )
        """
    )
    assert still_attached is False

    # Dropped: the table itself is gone, and querying events for these
    # trace_ids (which would previously have hit this partition) now
    # correctly finds nothing — the data left the hot store.
    table_exists = await db.pool.fetchval(
        "SELECT EXISTS (SELECT FROM pg_class WHERE relname = 'events_2020_01')"
    )
    assert table_exists is False
    for trace_id in trace_ids:
        count = await db.pool.fetchval("SELECT count(*) FROM events WHERE trace_id = $1", trace_id)
        assert count == 0

    # But it's not lost — it's in object storage, readable, and correct.
    key = retention._archive_key("events_2020_01")
    async with object_storage._session().client("s3", **object_storage._client_kwargs()) as s3:
        response = await s3.get_object(Bucket=config.object_storage_bucket, Key=key)
        body = await response["Body"].read()
    lines = body.decode("utf-8").strip().split("\n")
    assert len(lines) == 3
    archived_trace_ids = {json.loads(line)["trace_id"] for line in lines}
    assert archived_trace_ids == {str(t) for t in trace_ids}


async def test_list_partitions_older_than_excludes_current_and_default() -> None:
    eligible = await retention.list_partitions_older_than(retention_days=90)
    assert "events_default" not in eligible
    current_month_partition = f"events_{datetime.now(UTC).strftime('%Y_%m')}"
    assert current_month_partition not in eligible


async def test_run_retention_sweep_archives_eligible_partitions_and_ensures_next_month(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, _ = test_agent
    await db.pool.execute("SELECT bastion_ensure_events_partition('2021-06-01'::date)")
    trace_id = uuid.uuid4()
    await _insert_synthetic_old_event(agent_id, trace_id, datetime(2021, 6, 10, tzinfo=UTC))

    archived = await retention.run_retention_sweep(retention_days=90)
    assert "events_2021_06" in archived

    table_exists = await db.pool.fetchval(
        "SELECT EXISTS (SELECT FROM pg_class WHERE relname = 'events_2021_06')"
    )
    assert table_exists is False

    next_month = (datetime.now(UTC).date().replace(day=1) + timedelta(days=32)).replace(day=1)
    next_month_partition = f"events_{next_month.strftime('%Y_%m')}"
    next_month_exists = await db.pool.fetchval(
        "SELECT EXISTS (SELECT FROM pg_class WHERE relname = $1)", next_month_partition
    )
    assert next_month_exists is True
