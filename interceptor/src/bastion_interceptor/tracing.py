"""OpenTelemetry distributed tracing — U12 (v2 upgrade),
UPGRADE_ARCHITECTURE.md §14: "one trace_id followed across Agent SDK ->
Interceptor -> Policy evaluation -> Postgres write -> Kafka publish ->
Aggregator -> WebSocket -> Browser."

Naming, stated explicitly per §14's own warning ("this is a genuinely
different (and harder) trace than the agent-execution trace_id in
DATA_MODEL.md — be explicit in code and docs about which 'trace' you
mean"): an OTel trace has its own W3C-standard trace ID, generated and
propagated by the OTel SDK itself, completely independent of this
system's own `trace_id` column (a UUID this application mints for one
agent-execution run, stored in Postgres, unrelated to any tracing
protocol). The two are never the same value and are never meant to be —
correlating them for a human debugging a specific agent run is what
`bastion.trace_id` (a *span attribute*, set on the root span,
`_decide_and_record`'s call in main.py) is for: "which OTel trace does
this one Bastion trace_id correspond to" is answerable by searching
Jaeger for that attribute, without the two ID namespaces ever colliding
or being interchangeable.

Backend: Jaeger (`infra/docker/docker-compose.yml`'s `jaeger` service,
`jaegertracing/all-in-one`), which accepts OTLP/HTTP directly — no
separate OTel Collector needed, keeping this system's stack additions
minimal (UPGRADE_ARCHITECTURE.md §18's "not 30 technologies" framing).
"""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.propagate import inject
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .config import config

_configured = False


def configure_tracing(app: FastAPI, *, service_name: str) -> None:
    """Idempotent — safe to call more than once (tests that build the app
    more than once per process don't end up with duplicate exporters)."""
    global _configured
    if not _configured:
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
        # Passing `endpoint=` explicitly (vs. relying on the exporter's own
        # env-var fallback) means it's used verbatim, not auto-suffixed —
        # `/v1/traces` has to be appended here.
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{config.otel_exporter_otlp_endpoint}/v1/traces")
            )
        )
        trace.set_tracer_provider(provider)
        AsyncPGInstrumentor().instrument()  # type: ignore[no-untyped-call]
        _configured = True
    FastAPIInstrumentor.instrument_app(app)


def get_tracer() -> trace.Tracer:
    return trace.get_tracer("bastion_interceptor")


def capture_trace_context() -> dict[str, str]:
    """Called by db.insert_event, inside the original request where the
    OTel span is actually live — captures it as a small, JSON-serializable
    carrier dict, stored on the outbox_events row (migration 0013) for the
    outbox publisher (a separate background process with no request
    context of its own) to read back later and turn into Kafka headers via
    kafka_headers_from_context, below. This split — capture now, convert
    later — exists because the two happen in genuinely different
    processes/times: by the time the publisher's polling loop gets to this
    row, the original span has already ended, so there's nothing "current"
    left to inject from at that point."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


def kafka_headers_from_context(carrier: dict[str, str] | None) -> list[tuple[str, bytes]]:
    """The other half of capture_trace_context — called by the outbox
    publisher immediately before producing, turning the stored carrier
    back into the (key, bytes) header pairs aiokafka expects. `None`
    (a row written before this migration, or if tracing was ever disabled)
    degrades to no headers — the consumer then simply starts a new,
    disconnected trace for that message rather than failing."""
    if not carrier:
        return []
    return [(k, v.encode("utf-8")) for k, v in carrier.items()]
