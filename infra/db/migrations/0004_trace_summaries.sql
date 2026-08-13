-- Phase 4: trace_summaries (read-model/projection, rebuildable from
-- events — DATA_MODEL.md is explicit that if this table and `events` ever
-- disagree, `events` wins) + a NOTIFY trigger the aggregator LISTENs on.

CREATE TABLE trace_summaries (
    trace_id        uuid PRIMARY KEY,
    agent_id        uuid NOT NULL REFERENCES agents(id),
    org_id          uuid NOT NULL REFERENCES organizations(id),
    status          text NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'had_blocks')),
    total_cost      numeric NOT NULL DEFAULT 0,
    total_calls     int NOT NULL DEFAULT 0,
    blocked_calls   int NOT NULL DEFAULT 0,
    started_at      timestamptz NOT NULL,
    ended_at        timestamptz,
    graph_snapshot  jsonb NOT NULL
);

CREATE INDEX trace_summaries_org_id_started_at_idx ON trace_summaries(org_id, started_at DESC);

-- ARCHITECTURE.md §2.4/§2.5: the aggregator subscribes to the event stream
-- via "Postgres LISTEN/NOTIFY or a lightweight queue" — LISTEN/NOTIFY here,
-- since events already flows through Postgres and this needs no extra
-- infrastructure. Payload is minimal (well under NOTIFY's 8000-byte limit);
-- the aggregator re-fetches full event rows itself rather than trusting a
-- payload that could grow unbounded (a large tool-call payload could
-- otherwise blow the limit).
CREATE OR REPLACE FUNCTION bastion_notify_event() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('bastion_events', json_build_object(
        'trace_id', NEW.trace_id,
        'span_id', NEW.span_id,
        'event_type', NEW.event_type
    )::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER events_notify
    AFTER INSERT ON events
    FOR EACH ROW EXECUTE FUNCTION bastion_notify_event();
