-- U12 (v2 upgrade): carries the OTel trace context across the async gap
-- between "event written" (inside the original HTTP request, where the
-- OTel span is actually live) and "event published to Kafka" (the outbox
-- publisher's own background polling loop, a separate process with no
-- request context of its own — by the time it runs, the original span has
-- already ended). Captured once at insert_event time, read back and
-- attached as Kafka message headers at publish time — this is the real
-- mechanism that makes "Postgres write -> Kafka publish -> Aggregator"
-- one continuous OTel trace instead of two unrelated ones.

ALTER TABLE outbox_events ADD COLUMN otel_trace_context jsonb;
