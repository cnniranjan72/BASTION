-- Phase 3: approval_requests, per DATA_MODEL.md exactly.
--
-- resolved_by references users(id), but the users table doesn't exist yet
-- (Phase 5 builds auth). Same pattern as agents.default_policy_set_id in
-- 0001_init.sql: nullable now, FK constraint deferred to the Phase 5
-- migration once the referenced table exists.

CREATE TABLE approval_requests (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id        uuid NOT NULL,
    span_id         uuid NOT NULL,
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'denied', 'timed_out')),
    requested_at    timestamptz NOT NULL DEFAULT now(),
    resolved_by     uuid,
    resolved_at     timestamptz
);

CREATE INDEX approval_requests_span_id_idx ON approval_requests(span_id);
CREATE INDEX approval_requests_status_idx ON approval_requests(status);
