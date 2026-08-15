"""OpenTelemetry distributed tracing — U12 (v2 upgrade). Mirrors
interceptor/tracing.py exactly (same reasoning, same Jaeger backend, same
`bastion.trace_id`-as-span-attribute correlation convention) — see that
module's docstring for the full explanation of why this is a genuinely
different trace concept from this system's own agent-execution `trace_id`.

The one real addition here beyond the interceptor's version:
`extract_context_from_kafka_headers`, used by kafka_consumer.py to
continue the *same* OTel trace a message's producer (the interceptor's
outbox publisher) started, across the Kafka hop — the actual "genuinely
different and harder" part of this trace, per UPGRADE_ARCHITECTURE.md
§14's own framing. HTTP gets this for free from auto-instrumentation
(W3C traceparent headers); Kafka doesn't have an equivalent standard
built into aiokafka, so it's done by hand here using the same
OTel propagation API HTTP auto-instrumentation uses internally.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import FastAPI
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.propagate import extract
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .config import config

_configured = False


def configure_tracing(app: FastAPI, *, service_name: str) -> None:
    global _configured
    if not _configured:
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
        # U13 CI-fix/perf follow-up: mirrors interceptor/tracing.py's same
        # change -- see that module's comment for the real profiling
        # evidence behind it.
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{config.otel_exporter_otlp_endpoint}/v1/traces"),
                max_export_batch_size=config.otel_max_export_batch_size,
                schedule_delay_millis=config.otel_schedule_delay_millis,
            )
        )
        trace.set_tracer_provider(provider)
        AsyncPGInstrumentor().instrument()  # type: ignore[no-untyped-call]
        _configured = True
    FastAPIInstrumentor.instrument_app(app)


def get_tracer() -> trace.Tracer:
    return trace.get_tracer("bastion_aggregator")


def extract_context_from_kafka_headers(
    headers: Sequence[tuple[str, bytes]],
) -> otel_context.Context:
    """Called by kafka_consumer.py before processing each message — the
    returned Context, used as `start_as_current_span(..., context=...)`,
    makes the resulting span a *child of the original producer's span*,
    not a new, disconnected trace. This is the actual mechanism that
    makes "Postgres write -> Kafka publish -> Aggregator" one continuous
    trace instead of two unrelated ones that happen to reference the same
    data."""
    carrier = {k: v.decode("utf-8") for k, v in headers}
    return extract(carrier)
