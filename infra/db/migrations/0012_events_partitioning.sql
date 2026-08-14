-- U9 (v2 upgrade), UPGRADE_ARCHITECTURE.md §11: partition `events` by month.
-- This table is append-only and grows forever (every tool call, every
-- decision, every outcome) — plan for it now rather than retrofitting under
-- production load, per the spec's own framing.
--
-- Postgres can't convert an existing regular table into a partitioned one
-- in place — the standard approach (and what this migration does): rename
-- the old table aside, create a new partitioned table with the identical
-- shape, copy every row across, recreate the triggers/indexes (which,
-- since Postgres 11, only need to be defined once on the partitioned
-- parent — they're automatically inherited by every partition, existing
-- and future), then drop the old table.
--
-- Real constraint this ran into: Postgres requires every UNIQUE/PRIMARY KEY
-- constraint on a partitioned table to include the partition key column.
-- The original `events_id PRIMARY KEY` and `UNIQUE (trace_id,
-- sequence_number)` both become composite with `created_at` added —
-- functionally unchanged in practice (event_id is a fresh gen_random_uuid()
-- per row, already collision-proof; trace_id+sequence_number pairs are
-- assigned once with a fixed created_at), but worth stating plainly rather
-- than silently changing constraint shape.

ALTER TABLE events RENAME TO events_unpartitioned;
DROP TRIGGER events_no_update ON events_unpartitioned;
DROP TRIGGER events_no_delete ON events_unpartitioned;
DROP TRIGGER events_notify ON events_unpartitioned;
-- RENAME TABLE doesn't rename its indexes — every index-backed name below
-- would otherwise collide with the new table's same-named index/PK/UNIQUE
-- constraint further down, since (unlike CHECK/FOREIGN KEY constraint
-- names, which only need to be unique per-table) index names share the
-- schema-wide namespace with tables, sequences, and every other index.
ALTER INDEX events_agent_id_created_at_idx RENAME TO events_unpartitioned_agent_id_created_at_idx;
ALTER INDEX events_pkey RENAME TO events_unpartitioned_pkey;
ALTER INDEX events_trace_id_sequence_number_key RENAME TO events_unpartitioned_trace_id_sequence_number_key;

CREATE TABLE events (
    event_id            uuid NOT NULL DEFAULT gen_random_uuid(),
    trace_id            uuid NOT NULL,
    span_id             uuid NOT NULL,
    parent_span_id      uuid,
    agent_id            uuid NOT NULL REFERENCES agents(id),
    event_type          text NOT NULL CHECK (event_type IN (
                            'CallAttempted',
                            'PolicyEvaluated',
                            'CallAllowed',
                            'CallBlocked',
                            'CallPendingApproval',
                            'ApprovalGranted',
                            'ApprovalDenied',
                            'CallCompleted',
                            'CallFailed'
                         )),
    payload              jsonb NOT NULL DEFAULT '{}'::jsonb,
    sequence_number      bigint NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, created_at),
    UNIQUE (trace_id, sequence_number, created_at)
) PARTITION BY RANGE (created_at);

-- Default partition first: a safety net for any row outside the explicit
-- monthly ranges below (old fixture data, clock skew, anything
-- unanticipated) — without one, an INSERT for a date with no matching
-- partition would simply fail. A query whose date range falls entirely
-- within the contiguous named partitions below can still be pruned to
-- exclude this one; only genuinely out-of-range queries ever touch it.
CREATE TABLE events_default PARTITION OF events DEFAULT;

-- A full calendar year of monthly partitions, contiguous with no gaps —
-- covers 2026 (today, and every test in this repo's history and near
-- future) generously. The retention/archival job (see docs/adr/ADR-010)
-- is responsible for creating each new month's partition going forward,
-- ahead of when it's needed, not this migration.
CREATE TABLE events_2026_01 PARTITION OF events FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE events_2026_02 PARTITION OF events FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE events_2026_03 PARTITION OF events FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE events_2026_04 PARTITION OF events FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE events_2026_05 PARTITION OF events FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE events_2026_06 PARTITION OF events FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE events_2026_07 PARTITION OF events FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE events_2026_08 PARTITION OF events FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE events_2026_09 PARTITION OF events FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE events_2026_10 PARTITION OF events FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE events_2026_11 PARTITION OF events FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE events_2026_12 PARTITION OF events FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

INSERT INTO events (event_id, trace_id, span_id, parent_span_id, agent_id, event_type, payload, sequence_number, created_at)
SELECT event_id, trace_id, span_id, parent_span_id, agent_id, event_type, payload, sequence_number, created_at
FROM events_unpartitioned;

CREATE INDEX events_agent_id_created_at_idx ON events(agent_id, created_at);

-- Defined once on the partitioned parent — since Postgres 11, BEFORE ROW
-- triggers on a partitioned table are automatically inherited by every
-- partition (existing and future), no per-partition trigger needed.
CREATE TRIGGER events_no_update
    BEFORE UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION bastion_events_append_only();

CREATE TRIGGER events_no_delete
    BEFORE DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION bastion_events_append_only();

CREATE TRIGGER events_notify
    AFTER INSERT ON events
    FOR EACH ROW EXECUTE FUNCTION bastion_notify_event();

DROP TABLE events_unpartitioned;

-- bastion_next_sequence_number (0001_init.sql) queries `events WHERE
-- trace_id = $1` with no date filter — still correct on a partitioned
-- table (Postgres transparently scans every partition when the query
-- doesn't filter on the partition key), just without partition-pruning's
-- efficiency benefit for that specific query. Not a correctness change;
-- a trace's events essentially never span a month boundary in practice.

-- U9 (v2 upgrade): creates (or no-ops if it already exists) the partition
-- for a given month — the retention/archival job's forward-looking half.
-- SQL identifiers can't be parameterized, so the partition/bound literals
-- are built with format()/quote_ident() rather than string-concatenated
-- directly, avoiding any injection risk even though month_start's only
-- real callers pass a computed date, never raw user input.
CREATE OR REPLACE FUNCTION bastion_ensure_events_partition(month_start date) RETURNS void AS $$
DECLARE
    partition_name text := 'events_' || to_char(month_start, 'YYYY_MM');
    range_end date := month_start + interval '1 month';
BEGIN
    IF NOT EXISTS (SELECT FROM pg_class WHERE relname = partition_name) THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF events FOR VALUES FROM (%L) TO (%L)',
            partition_name, month_start, range_end
        );
    END IF;
END;
$$ LANGUAGE plpgsql;
