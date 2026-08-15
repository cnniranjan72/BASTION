"""U14 chaos scenario (UPGRADE_ARCHITECTURE.md §16): "Kill Kafka" —
required invariant: "audit trail still persists (outbox + Postgres),
publisher catches up when Kafka returns."

Real infrastructure, not a mock: stops and restarts the actual
`bastion-kafka` Docker container this test session's own OutboxPublisher
and KafkaEventConsumer are already running against (both started by
aggregator/tests/conftest.py's session-scoped `_event_pipeline` fixture) —
proving the *real* publisher retry/reconnect behavior, not a simulated
stand-in for it.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
import uuid
from uuid import UUID

import httpx
from bastion_interceptor.db import db as interceptor_db
from bastion_interceptor.main import app as interceptor_app

KAFKA_CONTAINER = "bastion-kafka"


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=interceptor_app), base_url="http://interceptor.test"
    )


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def _container_running(name: str) -> bool:
    result = _docker("inspect", "--format", "{{.State.Running}}", name)
    return result.returncode == 0 and result.stdout.strip() == "true"


async def _wait_until(predicate, *, timeout_seconds: float, poll_seconds: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(poll_seconds)
    return predicate()


async def test_writes_survive_kafka_outage_and_outbox_catches_up_on_recovery(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, raw_key = test_agent
    assert _container_running(KAFKA_CONTAINER), "expected bastion-kafka running before the test"

    stop = _docker("stop", KAFKA_CONTAINER)
    assert stop.returncode == 0, stop.stderr
    try:
        assert not _container_running(KAFKA_CONTAINER)

        trace_id = uuid.uuid4()
        body = {
            "trace_id": str(trace_id),
            "parent_span_id": None,
            "tool_name": "chaos.kafka_outage_test",
            "args": {},
            "agent_id": str(agent_id),
            "idempotency_key": str(uuid.uuid4()),
        }
        headers = {"Authorization": f"Bearer {raw_key}"}

        # The invariant's first half: the decision path itself never
        # touches Kafka (the transactional outbox writes to Postgres in
        # the same transaction as the decision — see interceptor/db.py's
        # insert_event), so /intercept must succeed exactly as if Kafka
        # were healthy.
        async with _http_client() as http:
            response = await http.post("/intercept", json=body, headers=headers)
        assert response.status_code == 200, response.text

        events = await interceptor_db.get_events_for_trace(trace_id)
        assert len(events) >= 1, "audit trail must persist in Postgres while Kafka is down"

        outbox_rows = await interceptor_db.get_outbox_events_for_trace(trace_id)
        assert len(outbox_rows) >= 1
        assert all(row["published_at"] is None for row in outbox_rows), (
            "expected the outbox row to still be queued (unpublished) while Kafka is down"
        )
    finally:
        start = _docker("start", KAFKA_CONTAINER)
        assert start.returncode == 0, start.stderr
        healthy = await _wait_until(lambda: _container_running(KAFKA_CONTAINER), timeout_seconds=60)
        assert healthy, "bastion-kafka did not come back up within 60s"
        # Give the broker time to finish its own internal startup (KRaft
        # controller election etc.) beyond just the container process
        # existing — the publisher's own retry loop handles this
        # gracefully either way, this just keeps the test's own timing sane.
        await asyncio.sleep(5)

    # The invariant's second half: once Kafka is back, the same
    # already-running OutboxPublisher (not a fresh one this test spun up)
    # must catch the backlog up on its own.
    deadline = time.monotonic() + 30
    published = False
    while time.monotonic() < deadline:
        rows = await interceptor_db.get_outbox_events_for_trace(trace_id)
        if rows and all(row["published_at"] is not None for row in rows):
            published = True
            break
        await asyncio.sleep(1)
    assert published, "outbox publisher did not catch the backlog up within 30s of Kafka recovery"
