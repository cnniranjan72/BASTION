"""U3 milestone tests (UPGRADE_BUILD_PLAN.md), the two Kafka-consumer-side
scenarios not covered by interceptor/tests/test_outbox_resumability.py
(which proves the *publisher* side):

1. "Kill the aggregator, restart, assert it resumes from committed offset
   and rebuilds identical state."
2. "Spin up a fresh analytics consumer from --from-beginning and assert it
   reprocesses full history correctly."

Both use their own throwaway consumer group_ids (never `"aggregator"`, the
real production group already running for the whole test session via
conftest.py's `_event_pipeline` fixture) — Kafka consumer groups are
independent by design, so a second, differently-named group reading the
same topic doesn't interfere with or get interfered by the first. That
independence is exactly what scenario 2 is demonstrating.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from uuid import UUID

from bastion_aggregator.db import db
from bastion_aggregator.graph import fold_events_to_graph
from bastion_aggregator.kafka_consumer import KafkaEventConsumer
from bastion_aggregator.main import _handle_notification
from bastion_interceptor.db import db as interceptor_db
from bastion_shared import EventType, TraceGraph


async def _write_call(
    *,
    trace_id: UUID,
    span_id: UUID,
    parent_span_id: UUID | None,
    agent_id: UUID,
    event_type: EventType,
    payload: dict[str, Any],
) -> None:
    await interceptor_db.insert_event(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        agent_id=agent_id,
        event_type=event_type,
        payload=payload,
    )


async def test_aggregator_consumer_resumes_from_committed_offset_after_restart(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, _ = test_agent
    trace_id = uuid.uuid4()
    root_span_id = uuid.uuid4()
    group_id = f"test-agg-resume-{uuid.uuid4()}"

    seen_before_crash: list[str] = []

    async def handler_a(message: dict[str, Any]) -> None:
        await _handle_notification(message)
        # Recorded *after* the real handler completes, so a test loop that
        # wakes up on this list seeing a new entry can also safely assume
        # any Postgres side effects that message triggers have landed.
        if message.get("trace_id") == str(trace_id):
            seen_before_crash.append(message["event_type"])

    # auto_offset_reset="latest", deliberately not the default "earliest":
    # this scenario is about offset-commit resumption between two instances
    # of the *same* group, not full-history replay (that's test 2, below,
    # on purpose) — "latest" skips whatever backlog already sits on the
    # topic from earlier local runs and starts this brand-new group right
    # at the current tail, so the only messages it will ever see are the
    # ones this test itself writes next.
    consumer_a = KafkaEventConsumer(group_id=group_id, auto_offset_reset="latest")
    await consumer_a.start(handler_a)
    try:
        # Let the initial group-join / position-fetch (which happens on
        # first consumption, not synchronously inside start()) settle
        # before writing anything, so "latest" resolves to the tail as of
        # now rather than racing our own writes below.
        await asyncio.sleep(1.0)

        await _write_call(
            trace_id=trace_id,
            span_id=root_span_id,
            parent_span_id=None,
            agent_id=agent_id,
            event_type=EventType.CALL_ATTEMPTED,
            payload={"tool_name": "resumability.agg.test", "args": {}},
        )
        await _write_call(
            trace_id=trace_id,
            span_id=root_span_id,
            parent_span_id=None,
            agent_id=agent_id,
            event_type=EventType.CALL_ALLOWED,
            payload={"policy_id": None, "decision": "allowed"},
        )

        # Wait for consumer_a to process both messages — proven by
        # observing CallAllowed, the second/last one, arrive at the wrapper.
        for _ in range(100):
            if "CallAllowed" in seen_before_crash:
                break
            await asyncio.sleep(0.1)
        assert seen_before_crash == ["CallAttempted", "CallAllowed"]

        # kafka_consumer.py commits *after* the handler returns, inside the
        # same _consume loop iteration our wrapper's append() ran in — by
        # the time the append is observable here, the commit() call has
        # already been issued; this short pause just lets it land before we
        # tear the consumer down, so the "crash" only ever discards
        # in-flight (uncommitted) work, never already-committed offsets.
        await asyncio.sleep(0.3)
    finally:
        await consumer_a.stop()

    # "Crash": write the trace's remaining event only *after* consumer_a is
    # gone, then bring up a brand-new KafkaEventConsumer instance under the
    # *same* group_id — no shared in-memory state with consumer_a, so any
    # progress must come from Kafka's own committed-offset bookkeeping.
    await _write_call(
        trace_id=trace_id,
        span_id=root_span_id,
        parent_span_id=None,
        agent_id=agent_id,
        event_type=EventType.CALL_COMPLETED,
        payload={"latency_ms": 12.5, "cost": None, "result": {"ok": True}},
    )

    seen_after_restart: list[str] = []

    async def handler_b(message: dict[str, Any]) -> None:
        await _handle_notification(message)
        if message.get("trace_id") == str(trace_id):
            seen_after_restart.append(message["event_type"])

    consumer_b = KafkaEventConsumer(group_id=group_id)
    await consumer_b.start(handler_b)
    try:
        # Generous window: the broker settles a rejoin of a group whose only
        # member just cleanly left (group.initial.rebalance.delay.ms-style
        # coordinator behavior) before this new member's partition
        # assignment/fetch position is actually ready — a real, if small,
        # source of latency distinct from the message-processing time this
        # test cares about proving is bounded and resumable.
        #
        # Waiting on *this test's own group* having processed the message
        # (seen_after_restart), not on trace_summaries being populated: the
        # real "aggregator" consumer group is also running for the whole
        # test session (conftest.py's `_event_pipeline`) and independently
        # consumes the same topic under its own group_id — it would persist
        # the same trace_summaries row on its own regardless of whether
        # *our* throwaway group_id ever catches up, which would make that
        # signal prove nothing about this test's actual subject.
        for _ in range(300):
            if seen_after_restart:
                break
            await asyncio.sleep(0.1)
    finally:
        await consumer_b.stop()

    # Resumed from the committed offset, not from `earliest`: the restarted
    # consumer only ever sees the one message it hadn't gotten to yet, never
    # a redelivery of the two consumer_a already committed.
    assert seen_after_restart == ["CallCompleted"]

    # Rebuilds identical state: the persisted summary matches a direct fold
    # of every event this trace ever wrote, straight from Postgres — the
    # same source of truth _handle_notification itself always re-derives
    # from, regardless of which specific message triggered it. (Whether
    # this row was written by our own consumer_b or by the concurrently
    # running "aggregator" group is irrelevant here — upsert_trace_summary
    # is idempotent and both paths fold the identical Postgres event
    # history, which is exactly the property being checked.)
    summary = await db.get_trace_summary(trace_id)
    assert summary is not None
    persisted_graph = TraceGraph.model_validate(summary["graph_snapshot"])
    all_events = await db.get_events_for_trace(trace_id)
    expected_graph = fold_events_to_graph(all_events)
    assert persisted_graph == expected_graph
    assert persisted_graph.status == "completed"


async def test_fresh_analytics_consumer_replays_full_history_from_beginning(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, _ = test_agent
    trace_id = uuid.uuid4()
    root_span_id = uuid.uuid4()

    await _write_call(
        trace_id=trace_id,
        span_id=root_span_id,
        parent_span_id=None,
        agent_id=agent_id,
        event_type=EventType.CALL_ATTEMPTED,
        payload={"tool_name": "resumability.analytics.test", "args": {}},
    )
    await _write_call(
        trace_id=trace_id,
        span_id=root_span_id,
        parent_span_id=None,
        agent_id=agent_id,
        event_type=EventType.CALL_ALLOWED,
        payload={"policy_id": None, "decision": "allowed"},
    )
    await _write_call(
        trace_id=trace_id,
        span_id=root_span_id,
        parent_span_id=None,
        agent_id=agent_id,
        event_type=EventType.CALL_COMPLETED,
        payload={"latency_ms": 4.0, "cost": None, "result": {"ok": True}},
    )

    # Every one of these events was written — and, via the session's
    # already-running OutboxPublisher, published to Kafka — *before* this
    # consumer group has ever existed. A brand-new group_id with
    # auto_offset_reset="earliest" (KafkaEventConsumer's default) is exactly
    # what "spin up a fresh analytics consumer from --from-beginning" means:
    # no prior offset to resume from, so it must replay the *entire* topic
    # history to find these three messages, same as the real
    # stub_consumers.py analytics/security groups would on first launch.
    group_id = f"test-analytics-fresh-{uuid.uuid4()}"
    seen: list[str] = []

    async def handler(message: dict[str, Any]) -> None:
        if message.get("trace_id") == str(trace_id):
            seen.append(message["event_type"])

    consumer = KafkaEventConsumer(group_id=group_id)
    await consumer.start(handler)
    try:
        # U13 finding: this bound was originally 200 (20s), which assumed a
        # small topic. U13's own k6 load test pushed the shared local
        # tool-events topic to 22K+ messages, and a real from-earliest full
        # replay against that volume took ~71s on this single-broker,
        # single-partition dev setup — confirmed by direct measurement, not
        # guessed. A production analytics consumer doing its first-ever
        # replay against real historical volume faces the same shape of
        # problem, so 20s was always an unstated small-topic assumption, not
        # a real bound on this test's own scenario. Widened to 1800 (180s);
        # the loop still exits the moment all 3 messages are seen, so this
        # costs nothing in CI's always-fresh, empty-topic case.
        for _ in range(1800):
            if len(seen) >= 3:
                break
            await asyncio.sleep(0.1)
    finally:
        await consumer.stop()

    assert seen == ["CallAttempted", "CallAllowed", "CallCompleted"]
