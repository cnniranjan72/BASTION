-- Phase 2: policy versioning.
--
-- Gap in DATA_MODEL.md, resolved here (see docs/ARCHITECTURE.md §10):
-- agents.default_policy_set_id is a single FK, but "policies are versioned,
-- never edited in place" means each version is a *new row* with its own id.
-- If default_policy_set_id pointed straight at a policies.id, activating a
-- new version could never change what an agent resolves to without manually
-- repointing every agent — breaking the "hot reload, no restart" milestone.
-- policy_sets gives each *name* a stable identity across versions; agents
-- point at the set, policies point at the set, and "the active version of
-- this set" is a query, not a fixed row reference.

CREATE TABLE policy_sets (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL REFERENCES organizations(id),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, name)
);

CREATE TABLE policies (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid NOT NULL REFERENCES organizations(id),
    policy_set_id   uuid NOT NULL REFERENCES policy_sets(id),
    name            text NOT NULL,
    version         int NOT NULL,
    definition      jsonb NOT NULL,
    active          boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (policy_set_id, version)
);

CREATE INDEX policies_org_id_idx ON policies(org_id);

-- At most one active version per policy set — the DB, not just application
-- logic, guarantees "activate" is exclusive within a set.
CREATE UNIQUE INDEX policies_one_active_per_set ON policies(policy_set_id) WHERE active;

-- Deferred from 0001_init.sql, which predates the policies table.
ALTER TABLE agents
    ADD CONSTRAINT agents_default_policy_set_id_fkey
    FOREIGN KEY (default_policy_set_id) REFERENCES policy_sets(id);
