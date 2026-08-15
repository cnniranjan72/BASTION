# ADR-019: OTel trace naming distinction and Kafka context propagation (unlisted, added U12)

## Status
Accepted

## Context
UPGRADE_ARCHITECTURE.md §14 asks for one OTel trace followed across the full call chain, using "a
distinctly-named OTel trace concept from the agent-execution `trace_id` already in DATA_MODEL.md" —
and warns explicitly: "do not let the two concepts collide under one name." Not in `ADR_INDEX.md`'s
required list (U12 doesn't name a required ADR the way most other phases do), but two genuinely
non-obvious decisions were made building this phase, worth recording per `ADR_INDEX.md`'s own "add
new ADRs as non-obvious decisions get made" instruction — same reasoning as ADR-017/018.

## Decision 1: naming
An OTel trace has its own W3C-standard trace ID, generated and propagated entirely by the OTel SDK —
completely independent of this system's own `trace_id` column (a UUID minted per agent-execution run,
stored in Postgres, unrelated to any tracing protocol). The two are never the same value and never
meant to be. Correlation for a human debugging one specific Bastion `trace_id` is `bastion.trace_id`
— a *span attribute* set on the request's root span (`_decide_and_record` in `interceptor/main.py`),
never a value substituted for the OTel trace ID itself. Finding "which OTel trace does this Bastion
trace_id belong to" means searching Jaeger for that attribute — a one-directional lookup, not a
claim the two IDs are interchangeable.

## Decision 2: Kafka context propagation, and why it needs its own mechanism
HTTP gets trace continuity for free: `opentelemetry-instrumentation-httpx` (the SDK) and
`opentelemetry-instrumentation-fastapi` (the interceptor/aggregator) both auto-propagate W3C
`traceparent` headers, so "Agent SDK → Interceptor" is one continuous trace with zero hand-written
code. Kafka has no equivalent built into `aiokafka`. Two options:
1. **Inject context at the point of Kafka production** (the outbox publisher's `publish_batch`
   loop). Rejected: by the time the publisher's background polling loop gets to a given row, the
   original request's OTel span has already ended and returned its HTTP response — there is no
   "currently active span" left to inject from at that point. This is the actual reason this is
   "genuinely different and harder" per §14's own framing, not just an API-shape inconvenience.
2. **Capture context at write time, propagate it through Postgres, inject at publish time**
   (chosen). Migration `0013_outbox_otel_context.sql` adds `outbox_events.otel_trace_context`;
   `db.insert_event` captures the *currently active* span's context (`tracing.capture_trace_context`,
   real inside a request, an empty no-op dict otherwise) at the one point it's actually available,
   and stores it alongside the row it belongs to. The outbox publisher reads it back
   (`tracing.kafka_headers_from_context`) and attaches it as Kafka message headers; the aggregator's
   consumer extracts it (`tracing.extract_context_from_kafka_headers`) and starts its `kafka.consume`
   span as a *child* of the original producer's span via `start_as_current_span(..., context=...)`.

## Consequences
- Proven directly, not just designed for: the milestone test
  (`aggregator/tests/test_observability.py`) fetches a real trace from Jaeger's REST API after a real
  request and asserts `kafka.consume` has a parent reference within the *same* trace as the manually-
  started root span — the actual claim this ADR makes, not an assumption the headers "should" work.
- A row written before this migration (or with tracing disabled) has `otel_trace_context = NULL`;
  `kafka_headers_from_context(None)` returns an empty header list, and the consumer's `extract()`
  on empty headers returns an empty context — the resulting span simply starts a new, disconnected
  trace instead of failing. A missing propagation context degrades to "less useful trace," never an
  error on the actual event-processing path.
- A real per-process test-environment quirk, documented rather than silently worked around:
  `opentelemetry.trace.set_tracer_provider()` is a process-wide global that's a no-op on any call
  after the first — in this repo's own cross-service tests (which import both `bastion_interceptor`
  and `bastion_aggregator` into one Python process), whichever service's `configure_tracing()` runs
  first "wins" the `Resource(SERVICE_NAME=...)` attached to the shared provider. This never happens
  in production (each service is its own process, its own provider), but means this repo's own test
  suite can't reliably distinguish spans by service-name resource in a cross-service test — the
  milestone test's assertions deliberately key off `operationName` and span references instead of
  service name for exactly this reason.

## Failure modes
Jaeger unreachable: `BatchSpanProcessor`'s export attempts fail in a background thread, logged but
never raised into application code — a tracing-backend outage degrades to "no traces recorded," never
a request failure, since `configure_tracing`'s exporter runs entirely off the request-handling path.
Postgres unreachable when the outbox publisher tries to read `otel_trace_context`: covered by the
outbox publisher's existing failure handling (ADR-003/010) — an unrelated, already-covered path, not
a new failure mode introduced here.
