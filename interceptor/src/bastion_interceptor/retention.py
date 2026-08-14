"""Retention + archival for `events` partitions — U9 (v2 upgrade),
UPGRADE_ARCHITECTURE.md §11: "hot events live in Postgres partitions;
older partitions get archived to object storage and detached."

Retention window: 90 days hot in Postgres (see docs/adr/ADR-010 for why —
short version: long enough to cover the realistic incident-investigation/
support window this system's traces exist for, short enough to bound
Postgres storage growth from an append-only table with no other cap).

This is a callable maintenance operation, not an automatically-scheduled
job — no scheduler infrastructure (cron, a k8s CronJob, Celery beat, ...)
exists anywhere in this project yet, and adding one is a deployment-
topology decision explicitly out of scope for this phase (same reasoning
as PgBouncer in ADR-010). `main()` below is a manually- or externally-
triggered entry point (`python -m bastion_interceptor.retention`), same
shape as outbox_publisher.py's.

Archival is genuinely destructive — `archive_and_detach_partition` uploads
every row to object storage, detaches the partition from `events` (it
stops being queryable through the parent table), and then drops it. Data
is never dropped without a prior, verified upload; verification here means
"the row count uploaded equals the row count in the partition," not a
byte-for-byte replay-and-compare (a stronger guarantee a future pass could
add).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import structlog

from . import object_storage
from .config import config
from .db import db

log = structlog.get_logger()

RETENTION_DAYS = 90


def _archive_key(partition_name: str) -> str:
    return f"archives/{partition_name}.jsonl"


async def list_partitions_older_than(retention_days: int = RETENTION_DAYS) -> list[str]:
    """Partitions whose entire range is older than the retention window —
    `events_default` is deliberately excluded (its range is unbounded, so
    "entirely older than X" can never be true for it) and the *current*
    month is never a candidate even at exactly `retention_days` (a
    partition is only eligible once its upper bound has fully passed)."""
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    rows = await db.pool.fetch(
        """
        SELECT c.relname
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        WHERE i.inhparent = 'events'::regclass
          AND c.relname != 'events_default'
        ORDER BY c.relname
        """
    )
    # Partition bounds aren't directly queryable as typed values from
    # relpartbound text portably across Postgres versions; deriving
    # eligibility from the partition name itself (events_YYYY_MM, the only
    # naming scheme this migration/bastion_ensure_events_partition ever
    # produces) is simpler and just as correct, since the two are always
    # kept in lockstep by construction.
    eligible = []
    for row in rows:
        name = row["relname"]
        try:
            _, year_str, month_str = name.split("_")
            partition_month_end = date(int(year_str), int(month_str), 1) + timedelta(days=32)
            partition_month_end = partition_month_end.replace(day=1)
        except (ValueError, IndexError):
            continue  # not an events_YYYY_MM partition — skip, don't guess
        if datetime(partition_month_end.year, partition_month_end.month, 1, tzinfo=UTC) <= cutoff:
            eligible.append(name)
    return eligible


async def archive_and_detach_partition(partition_name: str) -> int:
    """Uploads every row in `partition_name` to object storage as JSONL,
    verifies the uploaded row count matches, detaches it from `events`,
    then drops it. Returns the number of rows archived."""
    rows = await db.pool.fetch(f'SELECT * FROM "{partition_name}"')
    lines = [
        json.dumps(
            {
                "event_id": str(r["event_id"]),
                "trace_id": str(r["trace_id"]),
                "span_id": str(r["span_id"]),
                "parent_span_id": str(r["parent_span_id"]) if r["parent_span_id"] else None,
                "agent_id": str(r["agent_id"]),
                "event_type": r["event_type"],
                "payload": r["payload"],
                "sequence_number": r["sequence_number"],
                "created_at": r["created_at"].isoformat(),
            }
        )
        for r in rows
    ]
    body = "\n".join(lines).encode("utf-8")

    async with object_storage._session().client("s3", **object_storage._client_kwargs()) as s3:
        await s3.put_object(
            Bucket=config.object_storage_bucket,
            Key=_archive_key(partition_name),
            Body=body,
            ContentType="application/x-ndjson",
        )
        head = await s3.head_object(
            Bucket=config.object_storage_bucket, Key=_archive_key(partition_name)
        )
    if head["ContentLength"] != len(body):
        raise RuntimeError(
            f"archive upload verification failed for {partition_name}: "
            f"uploaded {len(body)} bytes, object storage reports {head['ContentLength']}"
        )

    async with db.pool.acquire() as conn, conn.transaction():
        await conn.execute(f'ALTER TABLE events DETACH PARTITION "{partition_name}"')
        await conn.execute(f'DROP TABLE "{partition_name}"')

    log.info("events partition archived and detached", partition=partition_name, rows=len(rows))
    return len(rows)


async def run_retention_sweep(retention_days: int = RETENTION_DAYS) -> list[str]:
    """One full pass: archive+detach every partition past the retention
    window, and ensure next month's partition exists (the forward-looking
    half — bastion_next_sequence_number/insert_event would otherwise start
    failing the moment a new month begins with no partition to route into).
    Returns the list of partition names archived."""
    next_month = (date.today().replace(day=1) + timedelta(days=32)).replace(day=1)
    await db.pool.execute("SELECT bastion_ensure_events_partition($1::date)", next_month)

    archived = []
    for partition_name in await list_partitions_older_than(retention_days):
        await archive_and_detach_partition(partition_name)
        archived.append(partition_name)
    return archived


async def main() -> None:
    await db.connect()
    try:
        archived = await run_retention_sweep()
        if archived:
            print(f"archived {len(archived)} partition(s): {', '.join(archived)}")
        else:
            print("no partitions past the retention window")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
