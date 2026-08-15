"""Kafka consumer — U3 (v2 upgrade), replaces v1's Postgres LISTEN/NOTIFY
(listener.py, kept in the tree but no longer wired into main.py's lifespan —
see docs/adr/ADR-002 for why LISTEN/NOTIFY was replaced rather than kept as
a fallback). Same `on_notification` callback shape as the old
`EventListener`, deliberately: `main.py`'s `_handle_notification` — which
re-fetches and re-folds the whole trace on every notification, idempotent
by construction — is completely unchanged. Only the trigger mechanism moved.

Manual offset commit, after successful processing, not before and not
auto-committed on a timer: a crash between receiving a message and
committing its offset causes that message to be redelivered on restart
(never silently skipped) — the resumability the U3 milestone test proves.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiokafka import AIOKafkaConsumer
from bastion_shared import TOOL_EVENTS_TOPIC, kafka_client_kwargs
from opentelemetry import trace as otel_trace

from . import tracing
from .config import config

log = structlog.get_logger()

NotificationHandler = Callable[[dict[str, Any]], Awaitable[None]]


class KafkaEventConsumer:
    def __init__(self, *, group_id: str, auto_offset_reset: str = "earliest") -> None:
        self._group_id = group_id
        self._auto_offset_reset = auto_offset_reset
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self, on_notification: NotificationHandler) -> None:
        self._consumer = AIOKafkaConsumer(
            TOOL_EVENTS_TOPIC,
            bootstrap_servers=config.kafka_bootstrap_servers,
            group_id=self._group_id,
            auto_offset_reset=self._auto_offset_reset,
            enable_auto_commit=False,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            **kafka_client_kwargs(
                security_protocol=config.kafka_security_protocol,
                sasl_mechanism=config.kafka_sasl_mechanism,
                sasl_username=config.kafka_sasl_username,
                sasl_password=config.kafka_sasl_password,
            ),
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume(on_notification))

    async def _consume(self, on_notification: NotificationHandler) -> None:
        assert self._consumer is not None
        async for message in self._consumer:
            # U12 (v2 upgrade): continues the *same* OTel trace the
            # original producer (interceptor's outbox publisher) started —
            # see tracing.py's module docstring. message.headers is
            # whatever kafka_headers_from_context attached (empty if the
            # row predates U12 or tracing was disabled), in which case
            # extract() returns an empty context and this span simply
            # starts its own new trace instead of continuing one — a safe
            # degrade, not an error.
            parent_context = tracing.extract_context_from_kafka_headers(message.headers or [])
            try:
                with tracing.get_tracer().start_as_current_span(
                    "kafka.consume", context=parent_context, kind=otel_trace.SpanKind.CONSUMER
                ):
                    await on_notification(message.value)
            except Exception:
                log.exception(
                    "kafka consumer failed to process message",
                    group_id=self._group_id,
                    value=message.value,
                )
                # Deliberately not committed — this message is redelivered
                # on the next poll/restart rather than silently skipped.
                continue
            await self._consumer.commit()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
