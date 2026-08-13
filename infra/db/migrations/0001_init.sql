-- Phase 1: organizations, agents, and the append-only events core.
-- Postgres 16 has gen_random_uuid() in core (no pgcrypto extension needed).

CREATE TABLE organizations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agents (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                  uuid NOT NULL REFERENCES organizations(id),
    name                    text NOT NULL,
    api_key_hash            text NOT NULL UNIQUE,
    -- References policies(id), added once the policies table exists (Phase 2 migration).
    default_policy_set_id   uuid,
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX agents_org_id_idx ON agents(org_id);

-- events: append-only, the event-sourcing core (DATA_MODEL.md).
CREATE TABLE events (
    event_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
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
    -- Doubles as the events(trace_id, sequence_number) index DATA_MODEL.md calls
    -- for, and as a hard backstop against a sequence-number race slipping past
    -- the advisory lock in bastion_next_sequence_number below.
    UNIQUE (trace_id, sequence_number)
);

CREATE INDEX events_agent_id_created_at_idx ON events(agent_id, created_at);

-- Append-only enforcement: reject UPDATE/DELETE at the DB layer, not just by
-- application discipline (DATA_MODEL.md: "No updates or deletes on this
-- table. Ever. Enforce with a DB trigger or role permission.").
CREATE OR REPLACE FUNCTION bastion_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'events is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER events_no_update
    BEFORE UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION bastion_events_append_only();

CREATE TRIGGER events_no_delete
    BEFORE DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION bastion_events_append_only();

-- Strictly-increasing per-trace sequence numbers under concurrent writers.
-- hashtextextended(..., 0) gives a 64-bit hash straight into
-- pg_advisory_xact_lock(bigint): different trace_ids essentially never
-- collide, and the same trace_id serializes only against itself, so
-- concurrent nested calls across *different* traces never block each other.
-- The lock is transaction-scoped (released automatically on commit/rollback)
-- and must be called inside the same transaction as the INSERT that consumes
-- its return value.
CREATE OR REPLACE FUNCTION bastion_next_sequence_number(p_trace_id uuid) RETURNS bigint AS $$
DECLARE
    next_seq bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(p_trace_id::text, 0));
    SELECT COALESCE(MAX(sequence_number), -1) + 1 INTO next_seq
    FROM events WHERE trace_id = p_trace_id;
    RETURN next_seq;
END;
$$ LANGUAGE plpgsql;
