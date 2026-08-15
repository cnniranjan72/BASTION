"""Outbox publisher — U3 (v2 upgrade), UPGRADE_ARCHITECTURE.md §4.1.

Polls outbox_events for unpublished rows, publishes each to Kafka's
tool-events topic (partitioned by trace_id, §4.3), then marks the whole
batch published. Resumability is entirely a property of Postgres state
(published_at IS NULL), not anything held in this process's memory — a
crash mid-batch just leaves some rows unpublished, and the next run (this
process restarted, or a fresh one) picks up exactly where the last one
left off via the same query, no separate offset store needed.

At-least-once, not exactly-once, by design: marking a batch published only
happens after every message in it was successfully sent, so a crash
partway through a batch can cause some already-sent messages to be
resent on restart — a real, accepted duplicate delivery
(UPGRADE_ARCHITECTURE.md §16's "duplicate a Kafka event -> downstream fold
is idempotent" chaos scenario is exactly this case). The other direction
never happens: a row is never marked published without a confirmed send,
so no event is ever silently lost.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import structlog
from aiokafka import AIOKafkaProducer
from bastion_shared import TOOL_EVENTS_TOPIC, kafka_client_kwargs

from . import tracing
from .config import config
from .db import db

log = structlog.get_logger()


class OutboxPublisher:
    def __init__(self, *, batch_size: int = 100) -> None:
        self._batch_size = batch_size
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=config.kafka_bootstrap_servers,
            key_serializer=lambda k: k.encode("utf-8") if k is not None else None,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            **kafka_client_kwargs(
                security_protocol=config.kafka_security_protocol,
                sasl_mechanism=config.kafka_sasl_mechanism,
                sasl_username=config.kafka_sasl_username,
                sasl_password=config.kafka_sasl_password,
            ),
        )
        await self._producer.start()

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish_batch(self) -> int:
        """Publishes up to `batch_size` unpublished rows; returns how many.
        A return of 0 means caught up — nothing currently pending."""
        assert self._producer is not None, "call start() first"
        rows = await db.get_unpublished_outbox_events(self._batch_size)
        if not rows:
            return 0

        published_ids: list[int] = []
        for row in rows:
            message: dict[str, Any] = {
                "event_id": str(row["event_id"]),
                "trace_id": str(row["trace_id"]),
                "span_id": str(row["span_id"]),
                "parent_span_id": str(row["parent_span_id"]) if row["parent_span_id"] else None,
                "agent_id": str(row["agent_id"]),
                "event_type": row["event_type"],
                "payload": row["payload"],
            }
            # Partition key = trace_id (§4.3): events in the same causal
            # execution land in the same partition and are ordered relative
            # to each other; no ordering guarantee across different
            # trace_ids is made or implied.
            headers = tracing.kafka_headers_from_context(row["otel_trace_context"])
            await self._producer.send_and_wait(
                TOOL_EVENTS_TOPIC, key=str(row["trace_id"]), value=message, headers=headers
            )
            published_ids.append(row["id"])

        await db.mark_outbox_events_published(published_ids)
        log.info("outbox batch published", count=len(published_ids))
        return len(published_ids)

    async def run_forever(self, *, poll_interval: float = 0.2) -> None:
        await self.start()
        try:
            while True:
                try:
                    count = await self.publish_batch()
                except asyncpg.exceptions.InterfaceError:
                    # The DB pool this publisher reads from is closing —
                    # graceful shutdown, not a real fault. Stop cleanly
                    # rather than crash with an unhandled exception; the
                    # next run (this process restarted) resumes from
                    # wherever published_at IS NULL picks back up, same as
                    # any other interruption.
                    log.info("outbox publisher stopping: database pool closing")
                    return
                if count == 0:
                    await asyncio.sleep(poll_interval)
        finally:
            await self.stop()


async def main() -> None:
    await db.connect()
    publisher = OutboxPublisher()
    try:
        await publisher.run_forever()
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
