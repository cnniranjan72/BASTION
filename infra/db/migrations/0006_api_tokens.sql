-- Post-launch: personal API tokens for programmatic access to the
-- management API. A third auth domain alongside agents.api_key_hash
-- (machine) and refresh_tokens (human browser session) — a human's own
-- long-lived credential for scripts/CI, hashed the same way as an agent
-- key (SHA-256, a lookup key not a password) since it's high-entropy
-- random, not user-chosen.

CREATE TABLE api_tokens (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid NOT NULL REFERENCES organizations(id),
    user_id         uuid NOT NULL REFERENCES users(id),
    name            text NOT NULL,
    token_prefix    text NOT NULL,
    token_hash      text NOT NULL UNIQUE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_used_at    timestamptz,
    revoked_at      timestamptz
);

CREATE INDEX api_tokens_org_id_idx ON api_tokens(org_id);
CREATE INDEX api_tokens_user_id_idx ON api_tokens(user_id);
