# BASTION — Data Model

## Design principle
Event sourcing for everything trace-related. Traditional CRUD for account/policy configuration. Don't blur the two — policies are mutable config, traces are immutable history.

## Tables

### `organizations`
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| name | text | |
| created_at | timestamptz | |

### `users`
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| org_id | uuid fk | |
| email | text unique | |
| password_hash | text | argon2id |
| role | enum | owner / admin / approver / viewer |
| created_at | timestamptz | |

### `refresh_tokens`
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| user_id | uuid fk | |
| token_hash | text | never store raw token |
| family_id | uuid | for rotation/reuse-detection, see AUTH.md |
| issued_at | timestamptz | |
| expires_at | timestamptz | |
| revoked_at | timestamptz nullable | |

### `agents`
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| org_id | uuid fk | |
| name | text | |
| api_key_hash | text | |
| default_policy_set_id | uuid fk | references `policy_sets(id)`, not a specific `policies` row — see below |
| created_at | timestamptz | |

### `policy_sets` (added in Phase 2, not in the original spec)
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| org_id | uuid fk | |
| name | text | unique per org |
| created_at | timestamptz | |

**Why this table exists**: the original spec has `agents.default_policy_set_id`
as a single FK, but also says policies are versioned as new rows ("never
edited in place") and that hot-reload must update running interceptor
behavior with no restart. Those three facts are in tension — if
`default_policy_set_id` pointed straight at one `policies.id`, activating a
new version could never change what an agent resolves to without manually
repointing every agent. `policy_sets` gives a policy *name* a stable
identity across versions: agents and policy rows both point at the set, and
"the active version of this set" is a query (`policies` filtered on
`policy_set_id` + `active`), not a fixed row reference. See
`docs/ARCHITECTURE.md` §10.

### `policies`
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| org_id | uuid fk | |
| policy_set_id | uuid fk | added in Phase 2, see `policy_sets` above |
| name | text | |
| version | int | policies are versioned, never edited in place |
| definition | jsonb | compiled policy DSL |
| active | boolean | at most one active row per `policy_set_id` (DB-enforced, partial unique index) |
| created_at | timestamptz | |

### `events` (append-only, the event-sourcing core)
| column | type | notes |
|---|---|---|
| event_id | uuid pk | |
| trace_id | uuid | groups a full agent run |
| span_id | uuid | this specific call |
| parent_span_id | uuid nullable | causal parent |
| agent_id | uuid fk | |
| event_type | enum | CallAttempted / PolicyEvaluated / CallAllowed / CallBlocked / CallPendingApproval / ApprovalGranted / ApprovalDenied / CallCompleted / CallFailed |
| payload | jsonb | tool name, args, policy decision reasoning, latency, cost, etc. |
| sequence_number | bigint | strictly increasing per trace, for ordering |
| created_at | timestamptz | |

No updates or deletes on this table. Ever. Enforce with a DB trigger or role permission, not just app-level discipline.

### `approval_requests`
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| trace_id | uuid | |
| span_id | uuid | |
| status | enum | pending / approved / denied / timed_out |
| requested_at | timestamptz | |
| resolved_by | uuid fk nullable | user_id — FK to `users(id)` deferred to Phase 5 (table doesn't exist until then, same pattern as `agents.default_policy_set_id`); always `null` until then |
| resolved_at | timestamptz nullable | |

### `trace_summaries` (read-model / projection, rebuildable from `events`)
| column | type | notes |
|---|---|---|
| trace_id | uuid pk | |
| agent_id | uuid fk | |
| org_id | uuid fk | |
| status | enum | running / completed / failed / had_blocks |
| total_cost | numeric | derived, cached |
| total_calls | int | derived, cached |
| blocked_calls | int | derived, cached |
| started_at | timestamptz | |
| ended_at | timestamptz nullable | |
| graph_snapshot | jsonb | cached node/edge layout for fast replay |

This table exists purely for query performance. If it's ever inconsistent with `events`, `events` wins — this table can always be rebuilt by replaying.

## Indexing notes (talk about these in interviews)
- `events(trace_id, sequence_number)` — the core replay query
- `events(agent_id, created_at)` — for recent-activity queries per agent
- `trace_summaries(org_id, started_at desc)` — dashboard "recent traces" list
- Consider partitioning `events` by month once volume grows — mention this even if you don't implement it; shows you understand operational lifecycle of an append-only table.
