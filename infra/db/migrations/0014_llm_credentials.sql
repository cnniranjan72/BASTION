-- U17: BYOK LLM provider credentials (docs/adr/ADR-022). A fourth secret
-- kind alongside agents.api_key_hash, api_tokens.token_hash, and refresh
-- tokens — but unlike those, this one must be recoverable in plaintext
-- (BASTION has to present it to OpenAI/Anthropic/Gemini on each call), so
-- it's encrypted (AES-256-GCM, application layer), never hashed.
--
-- Personal, not org-shared, same reasoning as api_tokens: a key belongs to
-- whichever human pasted it, not automatically visible to teammates.

CREATE TABLE llm_credentials (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid NOT NULL REFERENCES organizations(id),
    user_id         uuid NOT NULL REFERENCES users(id),
    provider        text NOT NULL CHECK (provider IN ('openai', 'anthropic', 'gemini')),
    label           text NOT NULL,
    key_ciphertext  bytea NOT NULL,
    key_nonce       bytea NOT NULL,
    key_last4       text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_used_at    timestamptz,
    revoked_at      timestamptz
);

CREATE INDEX llm_credentials_org_id_idx ON llm_credentials(org_id);
CREATE INDEX llm_credentials_user_id_idx ON llm_credentials(user_id);

-- Same RLS treatment as every other org_id-carrying table (migration 0010) —
-- defense-in-depth on top of, not instead of, application-layer WHERE
-- scoping (CLAUDE.md rule #7). Not FORCE'd differently from that
-- migration's precedent: same fail-closed behavior, no org context means
-- no rows.
ALTER TABLE llm_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_credentials FORCE ROW LEVEL SECURITY;
CREATE POLICY llm_credentials_org_isolation ON llm_credentials
    USING (org_id = current_setting('app.current_org_id', true)::uuid);
