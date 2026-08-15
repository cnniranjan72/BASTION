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

### `api_tokens` (added post-launch, not in the original spec — AUTH.md §4)
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| org_id | uuid fk | |
| user_id | uuid fk | personal, not org-shared — list/revoke are scoped to this, not `org_id` |
| name | text | user-chosen label, e.g. "CI pipeline" |
| token_prefix | text | first few chars of the raw token, for display in the list UI |
| token_hash | text unique | SHA-256, same reasoning as `agents.api_key_hash` |
| created_at | timestamptz | |
| last_used_at | timestamptz nullable | updated on every authenticated request the token makes |
| revoked_at | timestamptz nullable | |

### `llm_credentials` (added U17, not in the original spec — docs/adr/ADR-022)
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| org_id | uuid fk | |
| user_id | uuid fk | personal, not org-shared — same reasoning as `api_tokens` |
| provider | text | `openai` \| `anthropic` \| `gemini` |
| label | text | user-chosen, e.g. "personal OpenAI key" |
| key_ciphertext | bytea | AES-256-GCM — **reversible**, unlike `agents.api_key_hash`/`api_tokens.token_hash`, because BASTION must present the plaintext to the provider on each call |
| key_nonce | bytea | AES-GCM nonce, unique per encryption |
| key_last4 | text | for display in the list UI, never the full key |
| created_at | timestamptz | |
| last_used_at | timestamptz nullable | updated on each live-run call that uses this credential |
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
| event_id | uuid, part of composite pk `(event_id, created_at)` | see U9 note below for why `created_at` joined the key |
| trace_id | uuid | groups a full agent run |
| span_id | uuid | this specific call |
| parent_span_id | uuid nullable | causal parent |
| agent_id | uuid fk | |
| event_type | enum | CallAttempted / PolicyEvaluated / CallAllowed / CallBlocked / CallPendingApproval / ApprovalGranted / ApprovalDenied / CallCompleted / CallFailed |
| payload | jsonb | tool name, args, policy decision reasoning, latency, cost, etc. — OR, for a payload at/above `object_storage_payload_threshold_bytes` (U9, v2 upgrade, `docs/adr/ADR-011`), a small pointer `{"storage": "s3", "uri": ..., "hash": ..., "size_bytes": ...}` in place of the real content, which then lives in object storage instead |
| sequence_number | bigint, part of composite unique `(trace_id, sequence_number, created_at)` | strictly increasing per trace, for ordering |
| created_at | timestamptz | also the partition key, see below |

No updates or deletes on this table. Ever. Enforce with a DB trigger or role permission, not just app-level discipline.

**U9 (v2 upgrade)**: `events` is a partitioned table (`PARTITION BY RANGE (created_at)`, migration `0012`) — monthly partitions (`events_2026_01`, ...), a `DEFAULT` catch-all, `bastion_ensure_events_partition()` creates future months on demand. Postgres requires every UNIQUE/PRIMARY KEY constraint on a partitioned table to include the partition key, which is why `event_id`'s primary key and the `(trace_id, sequence_number)` uniqueness constraint both gained `created_at` — functionally unchanged (both were already effectively unique on their own), just formally composite now. Retention: 90 days hot, then archived to object storage and detached (`docs/adr/ADR-010`, `interceptor/retention.py`) — a callable maintenance operation, not yet wired to a scheduler.

### `approval_requests`
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| trace_id | uuid | |
| span_id | uuid | |
| status | enum | pending / approved / denied / timed_out |
| requested_at | timestamptz | |
| resolved_by | uuid fk nullable | user_id, FK added in Phase 5 migration (`0005_users_auth.sql`) once `users` exists |
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
- `events` partitioned by month (U9, v2 upgrade) — implemented, not just considered; see the `events` table's U9 note above and `docs/adr/ADR-010`.
