-- U8 (v2 upgrade): Postgres Row-Level Security as defense-in-depth for
-- multi-tenant isolation, on top of (not instead of) the application-layer
-- `WHERE org_id = ...` scoping every existing query already does
-- (CLAUDE.md rule #7). See docs/adr/ADR-009 for why this needs a separate,
-- non-superuser role: RLS is unconditionally bypassed for the superuser
-- role every other connection in this system uses (`bastion`, the
-- POSTGRES_USER bootstrap role) — no policy or FORCE ROW LEVEL SECURITY
-- setting can change that, it's a hard Postgres rule. `bastion_app` is a
-- second, restricted role created specifically so RLS has something to
-- actually apply to.
--
-- Idempotent role creation: roles are cluster-level objects, not scoped to
-- this database, so a persistent local Postgres volume across DB
-- resets could already have this role from a prior run.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bastion_app') THEN
        CREATE ROLE bastion_app LOGIN PASSWORD 'bastion_app';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO bastion_app;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO bastion_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO bastion_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO bastion_app;
-- So future migrations' new tables/sequences/functions are usable by
-- bastion_app too, without a manual re-GRANT accompanying every one of them.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO bastion_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON SEQUENCES TO bastion_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO bastion_app;

-- Scope, stated explicitly (ADR-009): RLS is enabled on every table that
-- carries `org_id` directly. `events`, `outbox_events`, `approval_requests`,
-- and `refresh_tokens` are NOT covered here — none has a direct `org_id`
-- column (only `agent_id`/`user_id`/`span_id`), and a correct policy for
-- them needs either a denormalized `org_id` column or a subquery-based
-- policy against `agents`/`users` — a real, larger follow-up, not silently
-- assumed covered by this migration.

ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations FORCE ROW LEVEL SECURITY;
CREATE POLICY organizations_isolation ON organizations
    USING (id = current_setting('app.current_org_id', true)::uuid);

ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents FORCE ROW LEVEL SECURITY;
CREATE POLICY agents_org_isolation ON agents
    USING (org_id = current_setting('app.current_org_id', true)::uuid);

ALTER TABLE policy_sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy_sets FORCE ROW LEVEL SECURITY;
CREATE POLICY policy_sets_org_isolation ON policy_sets
    USING (org_id = current_setting('app.current_org_id', true)::uuid);

ALTER TABLE policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE policies FORCE ROW LEVEL SECURITY;
CREATE POLICY policies_org_isolation ON policies
    USING (org_id = current_setting('app.current_org_id', true)::uuid);

ALTER TABLE trace_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE trace_summaries FORCE ROW LEVEL SECURITY;
CREATE POLICY trace_summaries_org_isolation ON trace_summaries
    USING (org_id = current_setting('app.current_org_id', true)::uuid);

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
CREATE POLICY users_org_isolation ON users
    USING (org_id = current_setting('app.current_org_id', true)::uuid);

ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_tokens FORCE ROW LEVEL SECURITY;
CREATE POLICY api_tokens_org_isolation ON api_tokens
    USING (org_id = current_setting('app.current_org_id', true)::uuid);

ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY idempotency_keys_org_isolation ON idempotency_keys
    USING (org_id = current_setting('app.current_org_id', true)::uuid);

-- current_setting(..., true) returns NULL when unset (rather than raising),
-- and `org_id = NULL` is NULL, which Postgres RLS treats as "row not
-- visible" — the fail-closed default: a connection that never calls
-- set_config('app.current_org_id', ...) sees nothing on these tables, not
-- everything. This is deliberate and matches bastion_app's very reason for
-- existing: it should never be used without an org context set.
