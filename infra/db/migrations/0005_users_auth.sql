-- Phase 5: users + refresh_tokens, per DATA_MODEL.md exactly.

CREATE TABLE users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid NOT NULL REFERENCES organizations(id),
    email           text NOT NULL UNIQUE,
    password_hash   text NOT NULL,
    role            text NOT NULL CHECK (role IN ('owner', 'admin', 'approver', 'viewer')),
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX users_org_id_idx ON users(org_id);

CREATE TABLE refresh_tokens (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id),
    token_hash      text NOT NULL UNIQUE,
    family_id       uuid NOT NULL,
    issued_at       timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    revoked_at      timestamptz
);

CREATE INDEX refresh_tokens_family_id_idx ON refresh_tokens(family_id);
CREATE INDEX refresh_tokens_user_id_idx ON refresh_tokens(user_id);

-- Deferred from 0003_approvals.sql, which predates the users table.
ALTER TABLE approval_requests
    ADD CONSTRAINT approval_requests_resolved_by_fkey
    FOREIGN KEY (resolved_by) REFERENCES users(id);
