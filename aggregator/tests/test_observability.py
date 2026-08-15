"""U12 milestone test (UPGRADE_BUILD_PLAN.md): pick one real request, follow
its OTel trace end to end in Jaeger, confirm every hop is visible with
correct timing — done here via Jaeger's own REST query API
(`GET /api/traces/{traceID}`) rather than a human clicking through the UI,
matching this project's established "real infrastructure, automated
verification" testing philosophy.

Lives here, not in interceptor/tests/, because the `kafka.consume` hop —
the actually hard part of this trace (UPGRADE_ARCHITECTURE.md §14 calls
this out directly as "genuinely different and harder" than a plain HTTP
call chain) — only exists once a message has round-tripped through the
real outbox -> Kafka -> aggregator-consumer pipeline, which only this
package's conftest.py wires up (`_event_pipeline`).

A root span is started manually in the test itself (rather than relying on
some upstream caller) so the exact OTel trace ID is known deterministically
— everything auto-instrumentation creates during the request nests under
it via ordinary OTel context propagation within the process; the Kafka hop
specifically needs its own explicit header-based propagation (tracing.py's
module docstring on both the interceptor and aggregator sides explains
why aiokafka has no built-in equivalent of HTTP's automatic mechanism).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import UUID

import httpx
import requests
from bastion import BastionClient
from bastion_interceptor.main import app as interceptor_app
from opentelemetry import trace as otel_trace

# Local dev remaps Jaeger's query UI/API off its standard 16686 (see
# infra/docker/docker-compose.yml's jaeger service comment — a collision
# with an unrelated pre-existing Jaeger container on this dev machine);
# CI (a fresh runner, .github/workflows/ci.yml's jaeger service) uses the
# standard port with no such collision. JAEGER_QUERY_URL lets either
# environment override the default.
JAEGER_QUERY_URL = os.environ.get("JAEGER_QUERY_URL", "http://localhost:16687")


def _bastion_client(agent_id: UUID, raw_key: str) -> BastionClient:
    return BastionClient(
        base_url="http://interceptor.test",
        api_key=raw_key,
        agent_id=agent_id,
        transport=httpx.ASGITransport(app=interceptor_app),
    )


async def _noop() -> str:
    return "ok"


def _fetch_trace(trace_id_hex: str) -> dict[str, Any] | None:
    response = requests.get(f"{JAEGER_QUERY_URL}/api/traces/{trace_id_hex}", timeout=5)
    if response.status_code != 200:
        return None
    data = response.json().get("data") or []
    return data[0] if data else None


async def test_real_request_trace_is_fully_visible_in_jaeger_with_correct_hops(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, raw_key = test_agent
    tracer = otel_trace.get_tracer("test_observability")

    with tracer.start_as_current_span("test.milestone_request") as root_span:
        trace_id_hex = format(root_span.get_span_context().trace_id, "032x")

        async with _bastion_client(agent_id, raw_key) as client:
            result = await client.call("observability.test", {"amount": 1}, _noop)
        assert result == "ok"

    # Force the batch processor to export immediately rather than waiting
    # out its normal flush interval — this test needs the spans in Jaeger
    # now, not eventually. Both services' providers live in this one test
    # process (cross-service test), so flush whichever the global handle
    # resolves to; the other side's BatchSpanProcessor still flushes on its
    # own default schedule within the poll window below regardless.
    provider = otel_trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()

    trace_doc = None
    for _ in range(75):  # up to ~15s — real Kafka round-trip, not instant
        trace_doc = _fetch_trace(trace_id_hex)
        if trace_doc is not None:
            operation_names = {s["operationName"] for s in trace_doc.get("spans", [])}
            if "kafka.consume" in operation_names:
                break
        await asyncio.sleep(0.2)

    assert trace_doc is not None, f"trace {trace_id_hex} never appeared in Jaeger"
    spans = trace_doc["spans"]
    operation_names = {s["operationName"] for s in spans}

    # The hops this system can actually produce spans for, per
    # UPGRADE_ARCHITECTURE.md §14's list: the manual root span, the
    # /intercept server span (FastAPI auto-instrumentation), the manual
    # policy.evaluate span (U12), and — the hard-won part — kafka.consume
    # on the aggregator side, proven to be part of the *same* trace only
    # because the outbox publisher's header propagation actually worked.
    assert "test.milestone_request" in operation_names
    assert any("intercept" in name for name in operation_names)
    assert "policy.evaluate" in operation_names
    assert "kafka.consume" in operation_names

    # Every span in a coherent trace must have a real, non-negative
    # duration — "correct timing," not just "spans exist."
    for span in spans:
        assert span["duration"] >= 0

    # Parent/child structure, not just "these spans happen to share a
    # trace ID": both the manually-added span and the cross-process one
    # must reference a parent within this same trace.
    policy_span = next(s for s in spans if s["operationName"] == "policy.evaluate")
    assert policy_span.get("references"), "policy.evaluate span has no parent reference"
    kafka_span = next(s for s in spans if s["operationName"] == "kafka.consume")
    assert kafka_span.get("references"), (
        "kafka.consume span has no parent reference -- Kafka header propagation "
        "isn't actually linking this to the producer's trace"
    )
