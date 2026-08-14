-- U2 (v2 upgrade): idempotency keys. UPGRADE_ARCHITECTURE.md §3 — the DB
-- unique constraint is the actual enforcement mechanism, not just app-level
-- logic. One row reserves (agent_id, idempotency_key) atomically; concurrent
-- identical requests race on the INSERT, exactly one wins, the rest read
-- the winner's stored response_body once it's populated.

CREATE TABLE idempotency_keys (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id              uuid NOT NULL REFERENCES organizations(id),
    agent_id            uuid NOT NULL REFERENCES agents(id),
    idempotency_key     text NOT NULL,
    trace_id            uuid NOT NULL,
    span_id             uuid NOT NULL,
    parent_span_id      uuid,
    status              text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed')),
    response_body       jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    completed_at        timestamptz
);

CREATE UNIQUE INDEX idempotency_keys_agent_key_idx ON idempotency_keys(agent_id, idempotency_key);
CREATE INDEX idempotency_keys_org_id_idx ON idempotency_keys(org_id);
