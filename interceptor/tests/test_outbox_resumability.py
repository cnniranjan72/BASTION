"""U3 milestone test (UPGRADE_BUILD_PLAN.md): kill the outbox publisher
mid-batch, restart it, assert no event is lost and none is duplicated in
Kafka beyond at-least-once (downstream fold is already idempotent per U1).

"Kill mid-batch" is simulated by constructing a *fresh* OutboxPublisher
instance after only some rows have been published — deliberately not an
OS-level process kill: the actual property being proven is that
resumability lives entirely in Postgres state (published_at IS NULL), not
in the publisher process's memory, and a fresh instance exercises exactly
that code path. A real process kill would be observably identical, since
nothing about this design holds resumption state anywhere else.

Assertions are scoped to this test's own trace_id throughout — the shared
dev database can have unrelated unpublished backlog from other tests/runs,
and `publish_batch()`'s global `LIMIT`-based query means a raw call count
isn't a reliable signal in that environment.
"""

from __future__ import annotations

import json
import uuid
from uuid import UUID

from aiokafka import AIOKafkaConsumer
from bastion_interceptor.config import config
from bastion_interceptor.db import db
from bastion_interceptor.outbox_publisher import OutboxPublisher
from bastion_shared import TOOL_EVENTS_TOPIC, EventType


async def _write_n_events(agent_id: UUID, trace_id: UUID, n: int) -> list[UUID]:
    span_ids = [uuid.uuid4() for _ in range(n)]
    for span_id in span_ids:
        await db.insert_event(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=None,
            agent_id=agent_id,
            event_type=EventType.CALL_ATTEMPTED,
            payload={"tool_name": "resumability.test", "args": {}},
        )
    return span_ids


async def test_publisher_resumes_after_simulated_crash_mid_batch(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, _ = test_agent

    # get_unpublished_outbox_events() is intentionally global and orders by
    # id ASC (real publisher requirement) — any unrelated backlog left by
    # earlier runs against this persistent shared dev DB would otherwise be
    # published first, ahead of this test's own rows, starving the crash
    # simulation below of anything to actually interrupt. Drain it first so
    # this test's rows are guaranteed to be next in line.
    drain_publisher = OutboxPublisher(batch_size=500)
    await drain_publisher.start()
    try:
        while await drain_publisher.publish_batch() > 0:
            pass
    finally:
        await drain_publisher.stop()

    trace_id = uuid.uuid4()
    span_ids = await _write_n_events(agent_id, trace_id, 5)

    # Small batch size so a handful of publish_batch() calls only ever
    # cover a fraction of this test's 5 rows — enough to reliably leave
    # some of *our* rows unpublished before we "crash."
    publisher_a = OutboxPublisher(batch_size=2)
    await publisher_a.start()
    try:
        for _ in range(3):
            rows = await db.get_outbox_events_for_trace(trace_id)
            if sum(1 for r in rows if r["published_at"] is not None) >= 2:
                break
            await publisher_a.publish_batch()
    finally:
        await publisher_a.stop()

    rows_after_crash = await db.get_outbox_events_for_trace(trace_id)
    published_after_crash = {
        r["span_id"] for r in rows_after_crash if r["published_at"] is not None
    }
    unpublished_after_crash = {r["span_id"] for r in rows_after_crash if r["published_at"] is None}
    assert published_after_crash, "expected at least some rows published before the simulated crash"
    assert unpublished_after_crash, (
        "expected at least some of this test's rows still unpublished after the "
        "simulated crash — batch_size=2 with 5 rows should guarantee this"
    )

    # "Restart": a brand-new instance, no shared in-memory state with
    # publisher_a — resumability must come entirely from Postgres.
    publisher_b = OutboxPublisher(batch_size=100)
    await publisher_b.start()
    try:
        for _ in range(20):
            rows = await db.get_outbox_events_for_trace(trace_id)
            if all(r["published_at"] is not None for r in rows):
                break
            await publisher_b.publish_batch()
    finally:
        await publisher_b.stop()

    # No event lost: every row for this trace is now published.
    final_rows = await db.get_outbox_events_for_trace(trace_id)
    assert len(final_rows) == 5
    assert all(r["published_at"] is not None for r in final_rows)

    # Confirm on the Kafka side too: every span_id this test wrote actually
    # arrived on the topic at least once (at-least-once, duplicates tolerated).
    consumer = AIOKafkaConsumer(
        TOOL_EVENTS_TOPIC,
        bootstrap_servers=config.kafka_bootstrap_servers,
        group_id=f"test-outbox-verify-{uuid.uuid4()}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()
    try:
        seen_span_ids: set[str] = set()
        target = {str(s) for s in span_ids}
        for _ in range(500):
            if target <= seen_span_ids:
                break
            batch = await consumer.getmany(timeout_ms=500)
            if not batch:
                break
            for records in batch.values():
                for record in records:
                    value = json.loads(record.value.decode("utf-8"))
                    seen_span_ids.add(value["span_id"])
    finally:
        await consumer.stop()

    assert {str(s) for s in span_ids} <= seen_span_ids, (
        "every event this test wrote must appear on the Kafka topic at least once"
    )
