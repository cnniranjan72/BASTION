-- U3 (v2 upgrade): transactional outbox. UPGRADE_ARCHITECTURE.md §4.1 — every
-- write to `events` that needs distribution also writes a row here, in the
-- SAME transaction (see interceptor/db.py's insert_event). A separate
-- publisher process polls unpublished rows and publishes to Kafka; if it
-- crashes mid-batch it resumes from wherever `published_at IS NULL` picks
-- back up — Postgres itself is the resume point, no separate offset store.

CREATE TABLE outbox_events (
    id              bigserial PRIMARY KEY,
    event_id        uuid NOT NULL,
    trace_id        uuid NOT NULL,
    span_id         uuid NOT NULL,
    agent_id        uuid NOT NULL,
    event_type      text NOT NULL,
    payload         jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    published_at    timestamptz
);

-- Partial index: the publisher's poll query only ever cares about
-- unpublished rows, and this index stays small forever (published rows
-- fall out of it) regardless of how large the table grows.
CREATE INDEX outbox_events_unpublished_idx ON outbox_events (id) WHERE published_at IS NULL;
