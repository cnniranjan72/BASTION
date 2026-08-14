"""U3 (v2 upgrade): `analytics` and `security` consumer groups on
tool-events, proving the fan-out pattern (UPGRADE_ARCHITECTURE.md §4.4)
works with multiple independent consumer groups reading the same topic —
each gets the full stream independently and can replay it from the
beginning on its own, which is the actual point of consumer groups over a
single queue, as opposed to (say) two workers splitting one queue.

Real stubs, not fake ones: each genuinely connects to Kafka, consumes every
message, and commits its own offset — the only thing "stubbed" is the
downstream business logic (real analytics aggregation, real security
pattern detection), which is out of scope for U3 on purpose. U3 proves the
distribution mechanism; FRONTEND_V2.md's Analytics/Threat Center screens
are where real logic eventually lands on top of this, once something in
the roadmap actually needs it — not built speculatively here.

Run: `python -m bastion_aggregator.stub_consumers analytics` or `security`.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import structlog

from .config import config
from .kafka_consumer import KafkaEventConsumer

log = structlog.get_logger()


async def _log_message(group_id: str, message: dict[str, Any]) -> None:
    log.info(
        "stub consumer received event",
        group_id=group_id,
        event_type=message.get("event_type"),
        trace_id=message.get("trace_id"),
    )


async def run_stub_consumer(group_id: str, *, auto_offset_reset: str = "earliest") -> None:
    consumer = KafkaEventConsumer(group_id=group_id, auto_offset_reset=auto_offset_reset)
    await consumer.start(lambda message: _log_message(group_id, message))
    log.info(
        "stub consumer started",
        group_id=group_id,
        bootstrap_servers=config.kafka_bootstrap_servers,
    )
    try:
        await asyncio.Event().wait()  # run until killed
    finally:
        await consumer.stop()


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("analytics", "security"):
        print("usage: python -m bastion_aggregator.stub_consumers <analytics|security>")
        raise SystemExit(1)
    asyncio.run(run_stub_consumer(sys.argv[1]))


if __name__ == "__main__":
    main()
