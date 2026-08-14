-- U8 follow-up: real bug found while writing the milestone test for
-- migration 0010's RLS policies.
--
-- `current_setting('app.current_org_id', true)` does NOT reliably return
-- NULL when the org context hasn't been set on the current connection.
-- `app.current_org_id` is a "placeholder" GUC (Postgres's term for a custom
-- variable not tied to a loaded extension) — the first time any session
-- calls `set_config('app.current_org_id', ..., true)` (LOCAL/transaction-
-- scoped, exactly what org_scoped_connection does), Postgres registers a
-- placeholder for that name. When the transaction ends, the LOCAL setting
-- reverts — but a placeholder variable's reset value is an EMPTY STRING,
-- not NULL. On a *pooled* connection reused across requests (asyncpg pools
-- do exactly this), any later query on that same physical connection that
-- forgets to call set_config again would hit `''::uuid`, which raises
-- `invalid input syntax for type uuid`, not silently return zero rows.
--
-- An error is not a data leak, but it's the wrong failure mode: it turns
-- "someone forgot to org-scope this connection" into a 500 instead of the
-- intended fail-closed "sees nothing." NULLIF(..., '') collapses both the
-- true-NULL case (a connection that has genuinely never touched this GUC)
-- and the reset-to-placeholder-empty-string case into the same safe NULL,
-- which `org_id = NULL` correctly treats as "no rows visible," no error.

ALTER POLICY organizations_isolation ON organizations
    USING (id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);

ALTER POLICY agents_org_isolation ON agents
    USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);

ALTER POLICY policy_sets_org_isolation ON policy_sets
    USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);

ALTER POLICY policies_org_isolation ON policies
    USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);

ALTER POLICY trace_summaries_org_isolation ON trace_summaries
    USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);

ALTER POLICY users_org_isolation ON users
    USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);

ALTER POLICY api_tokens_org_isolation ON api_tokens
    USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);

ALTER POLICY idempotency_keys_org_isolation ON idempotency_keys
    USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);
