# BASTION — Progress Log

## Status: Phase 3 complete
Phase: 3 (approval flow) → next up: Phase 4 (trace aggregator + replay API)

## Log
- [2026-08-14] Project specced out (PRD, ARCHITECTURE, DATA_MODEL, AUTH, API_SPEC, BUILD_PLAN written). No code yet.
- [2026-08-14] Phase 0 complete:
  - Repo initialized (`git init`), directory layout per BUILD_PLAN.md: `interceptor/`, `aggregator/`,
    `frontend/` (placeholder only), `sdk-python/`, `shared/`, `infra/`, `docs/`.
  - **Language pivot**: started Phase 0 on Node/TypeScript (Fastify) for interceptor+aggregator with a
    Zod `shared` package, then switched to **Python + FastAPI** at the user's request before any real
    logic existed (only health-check skeletons) — cheap to redo, so did the full rework rather than a
    partial one. Reasoning recorded in `docs/ARCHITECTURE.md` §7: one language across the whole
    non-UI backend (SDK, interceptor, aggregator, and the Phase 8 demo agent are all Python), and
    FastAPI's auto-generated OpenAPI schema gives Phase 11's "flag API_SPEC.md drift" deliverable a
    concrete artifact instead of a hand-written comparison.
  - `shared/` is now `bastion_shared`, a Pydantic package that `interceptor`, `aggregator`, and
    `sdk-python` import directly (not codegen) — events, policy DSL, `/intercept` request/response,
    error envelope, and `/live` WS messages all modeled, mirroring DATA_MODEL.md and API_SPEC.md
    field-for-field.
  - `interceptor` and `aggregator`: FastAPI skeletons with `/healthz`, structured logging
    (`structlog`), and a `request_id` middleware per CLAUDE.md rule #2.
  - Python monorepo tooling: `uv` workspace, `ruff`+`mypy --strict` clean, `pytest`.
  - Docker Compose: Postgres 16 + Redis 7, both healthy. **Postgres on host port 5442, not 5432**
    (collision with another local project) — documented in ARCHITECTURE.md §7.
  - CI (`.github/workflows/ci.yml`): `uv sync` → `ruff check` → `ruff format --check` → `mypy` →
    `pytest`, against live Postgres+Redis service containers. Not yet pushed/run on GitHub.
  - No "Warden" references found anywhere in `docs/`.
- [2026-08-14] **Credentials provisioned for Phase 9 (deployment), ahead of schedule**: user supplied
  a Neon Postgres connection string and a Render API key mid-Phase-1. Both verified working (Neon:
  live `SELECT version()`; Render: `render workspace current`). Stored only in the gitignored root
  `.env` (never committed, never printed after initial verification); `.env.example` documents the
  variable names with no real values. **Decision, confirmed with user**: local dev and the test suite
  keep using Docker Compose Postgres, not Neon — Neon is reserved for the actual deployed environment
  in Phase 9. Recorded in `docs/ARCHITECTURE.md` §7.
- [2026-08-14] Phase 1 complete:
  - Migration runner (`infra/db/migrate.py`, no ORM/Alembic — numbered `.sql` files in
    `infra/db/migrations/`, tracked in a `schema_migrations` table). `0001_init.sql` creates
    `organizations`, `agents` (FK to `policies` deferred to Phase 2 — table doesn't exist yet), and
    `events` with the two indexes DATA_MODEL.md calls for.
  - **Append-only enforcement**: `BEFORE UPDATE/DELETE` triggers on `events` that unconditionally
    `RAISE EXCEPTION`. Verified by hand against live Postgres: `UPDATE`/`DELETE` both rejected; as a
    side effect, deleting the test agent/org also correctly fails once it has events (FK + immutable
    events means the row genuinely cannot be un-created).
  - **Per-trace sequence numbers under concurrency**: `bastion_next_sequence_number(trace_id)` Postgres
    function using a transaction-scoped advisory lock (`pg_advisory_xact_lock(hashtextextended(...))`)
    so same-trace inserts serialize while different-trace inserts never contend. Backstopped by a
    `UNIQUE (trace_id, sequence_number)` constraint. Full reasoning in `docs/ARCHITECTURE.md` §9.
  - **Spec deviation, flagged and resolved**: ARCHITECTURE.md §2.2 describes the interceptor itself
    executing the real downstream call when a call is allowed, but neither `InterceptRequest` nor
    DATA_MODEL.md gives it anything to reach a downstream system with. Decision: the interceptor
    decides + logs only; the SDK executes the real call locally (only ever invoking it on `allowed`,
    which is the actual mechanism that prevents a blocked call from running) and reports the outcome
    via a new `POST /spans/{span_id}/complete` endpoint. Documented in `API_SPEC.md` and
    `docs/ARCHITECTURE.md` §8, not silently implemented.
  - `POST /intercept`: Bearer agent-API-key auth (SHA-256 lookup hash per AUTH.md §1),
    request-body `agent_id` must match the authenticated agent (`AGENT_MISMATCH`, 403) — the key
    determines identity, the body field is a stated-identity check. Hardcoded policy
    (`interceptor/src/bastion_interceptor/policy.py`): blocks `DELETE` queries against the
    `production` database, allows everything else. Emits `CallAttempted` → `CallAllowed`/`CallBlocked`.
  - `POST /spans/{span_id}/complete`: only valid for a span `/intercept` most recently allowed;
    emits `CallCompleted`/`CallFailed`.
  - Real Python SDK (`sdk-python/bastion/`): `BastionClient.call(tool_name, args, execute)` — `execute`
    is only invoked on `allowed`. Trace/span context propagated via a `contextvar`
    (`bastion/context.py`), so nested `call()`s inside an `execute` callback automatically inherit the
    right parent, and `asyncio.gather`'d concurrent children each get an independent context copy —
    no manual trace_id/span_id threading required by calling code.
  - **Milestone test passes**
    (`interceptor/tests/test_causal_ordering.py::test_concurrent_nested_calls_reconstruct_causal_graph`):
    real SDK, real FastAPI app (via `httpx.ASGITransport`, not mocked), real Postgres. Root call fans
    out 8 concurrent children, each with its own concurrent grandchild (17 spans total). Asserts:
    zero gaps/duplicates in `sequence_number` under concurrent writers on one trace, correct
    parent/child span reconstruction from `parent_span_id`, and correct per-span sub-event ordering
    (`CallAttempted` → `CallAllowed` → `CallCompleted`). All 4 interceptor tests + full 11-test
    workspace suite pass; `ruff`/`mypy --strict` clean.
  - Fixed along the way: pytest-asyncio defaults to a fresh event loop per test function, which broke
    the session-scoped asyncpg pool ("attached to a different loop"). Fixed via
    `asyncio_default_fixture_loop_scope = "session"` / `asyncio_default_test_loop_scope = "session"`
    — had to be set in **each package's own** `pyproject.toml`, not just the root one, since pytest
    uses the nearest ini file found, not a merge of all of them up the tree.

- [2026-08-14] Phase 2 complete:
  - **Spec gap, flagged and resolved**: `agents.default_policy_set_id` (single FK) + "policies
    versioned as new rows, never mutated" + "hot reload, no restart" don't fit together — activating
    a new version couldn't change what an agent resolves to without repointing every agent. Added
    `policy_sets` (stable identity per policy *name*, org-unique) — `policies.policy_set_id` and
    `agents.default_policy_set_id` both reference it; "the active policy" is always a query, never a
    fixed row reference. Partial unique index (`policies(policy_set_id) WHERE active`) makes
    "one active version per set" a DB guarantee. Documented in `docs/DATA_MODEL.md` and
    `docs/ARCHITECTURE.md` §10. Migration `0002_policies.sql`.
  - **Safe condition evaluator** (`interceptor/src/bastion_interceptor/policy.py`): condition
    expressions ("amount > 50") parsed via `ast.parse` and walked by a hand-rolled interpreter over an
    allow-listed node-type set — never `eval()`. Rejects `__import__`, `open`, lambdas, comprehensions,
    attribute access, at *compile* time (`POST /policies`, before a bad policy is ever activated), with
    a proper `INVALID_POLICY_CONDITION` 400 error envelope, not a raw 500.
  - Policy compiler: YAML/JSON-shaped `PolicyDefinition` → `CompiledPolicy` (precompiled regex +
    condition AST per rule, first-match-wins evaluation, implicit trailing allow if nothing matches —
    matches the ARCHITECTURE.md §2.3 example's own trailing `"*" -> allow` convention).
  - In-memory `PolicyCache` keyed by `policy_set_id`, bootstrapped from all `active` policies at
    interceptor startup, hot-reloaded via Redis pub/sub (`redis_bus.py`, channel
    `bastion:policy_updates`) — `POST /policies/{id}/activate` updates its own process's cache
    synchronously, then publishes so every *other* running instance picks it up too.
  - `POST /intercept` rewired: real policy lookup (`agent.default_policy_set_id` → cache) replaces
    Phase 1's hardcoded rule. `require_approval` fails closed (blocked, reason states approval flow
    lands in Phase 3) rather than half-implementing the workflow — safe default, not silently ignored.
  - **Spec gap, flagged and resolved**: `GET/POST /policies` are dashboard endpoints, but BUILD_PLAN.md
    schedules dashboard auth for Phase 5 specifically. Rather than build auth early (out of order) or
    skip multi-tenancy scoping until Phase 5 (violates CLAUDE.md rule #7 "from day one"), these
    endpoints take an explicit `org_id` request field for now; Phase 5 only changes where `org_id`
    comes from. Documented in `docs/ARCHITECTURE.md` §11. Multi-tenancy isolation test written now
    against this shape (`test_org_cannot_read_another_orgs_policies`), not deferred.
  - **Milestone test passes**
    (`test_hot_reload_propagates_via_pubsub_within_a_few_seconds`): a *second*, independent
    `PolicyCache`+`RedisBus` (simulating another interceptor instance) picks up a policy
    created+activated via the real API within seconds, over real Redis pub/sub — not just "the same
    process updated its own dict." 8 new tests total (safe-evaluator unit tests, versioning, intercept
    integration, hot reload, multi-tenancy isolation); full 21-test workspace suite passes,
    `ruff`/`mypy --strict` clean.
  - **Fixed along the way**: the `redis` client (8.1.0, very new) defaults to RESP3 protocol
    negotiation via `HELLO` on connect, which failed in this environment ("unknown command 'HELLO'")
    even though the server supports HELLO fine via `redis-cli` directly — looks like a client-side
    async I/O quirk on Windows, not a real server capability gap. Fixed by pinning `protocol=2` (RESP2)
    on the connection; plain pub/sub never needed RESP3 anyway. Also added a session-scoped fixture to
    connect the real `redis_bus` for tests (parallel to the existing `db` pool fixture) — nothing did
    this before, so `/policies/{id}/activate`'s publish call was hitting an unconnected client.
  - Also added: DB-layer `jsonb` type codec (`set_type_codec` on the asyncpg pool) so callers pass
    plain dicts/lists instead of hand-rolling `json.dumps`/`loads` around every jsonb column —
    refactored in alongside the new policy queries since there are now several.

- [2026-08-14] Phase 3 complete:
  - **Spec tension, flagged and resolved**: ARCHITECTURE.md §2.2 describes the interceptor itself
    long-polling/webhook-waiting for an approval decision, but API_SPEC.md separately defines
    `GET /approvals/{id}` whose own doc comment says the SDK long-polls *that*. Holding `/intercept`
    open for a human-timescale (potentially minutes) decision would also break the stateless,
    horizontally-scalable hot-path story. Decision: `/intercept` returns `pending_approval` immediately
    (never blocks); `GET /approvals/{id}` is the real long-poll target, woken by a Redis pub/sub signal
    (not busy-polling Postgres) but always re-checking Postgres as the source of truth after the wait
    either way. Documented in `docs/ARCHITECTURE.md` §13 and `API_SPEC.md`.
  - `approval_requests` table (migration `0003_approvals.sql`) exactly per DATA_MODEL.md;
    `resolved_by` FK to `users(id)` deferred to Phase 5 (table doesn't exist yet), same pattern as
    `agents.default_policy_set_id` in Phase 1.
  - **Timeout mechanism**: an absolute deadline (`APPROVAL_TTL_SECONDS`, default 300s) checked lazily
    on each `GET /approvals/{id}` call rather than a background sweeper — the same request that would
    otherwise report "still pending" instead atomically flips the row to `timed_out`
    (`db.expire_stale_approval`, guarded so only one caller ever wins) and emits `ApprovalDenied`.
  - `POST /policies/{id}/approve` / `/deny`: only transition a genuinely `pending` row (409
    `APPROVAL_NOT_PENDING` otherwise — two approvers racing isn't a silent overwrite), emit
    `ApprovalGranted`/`ApprovalDenied`, publish the Redis wake-up signal. `resolved_by` always `null`
    until Phase 5 (no `users` table).
  - **Event modeling fix**: a span resolved via approval never gets its own `CallAllowed` event —
    `ApprovalGranted` *is* that decision. Had to extend `db.get_span_decision` (used by
    `POST /spans/{id}/complete`) to treat `ApprovalGranted`/`ApprovalDenied` as the allowed/blocked
    equivalents — caught by the milestone test itself (`/spans/.../complete` 404'd on approved-then-
    executed calls until this was fixed).
  - SDK (`sdk-python/bastion/client.py`): `pending_approval` no longer raises immediately — `call()`
    polls `GET /approvals/{id}` in a loop (server-side long-poll each time) until resolved or its own
    `approval_max_wait` budget (default 60s) elapses, then either proceeds to `execute()` (approved) or
    raises `BastionBlockedError` (denied/timed_out/budget exceeded) — `execute()` is never invoked on
    any non-approved outcome. Removed the now-dead `BastionPendingApprovalError`.
  - Minimal plain HTML/JS approver page at `GET /approvals-ui` (BUILD_PLAN.md's own "not the 3D view
    yet" framing for this phase) — org_id text field (no auth yet), table of pending approvals,
    approve/deny buttons calling the real JSON API. No build step, no frontend/ toolchain involved.
  - **Milestone tests pass** (`interceptor/tests/test_approval_flow.py`, 3 tests): (1) a paused SDK call
    genuinely blocks (`execute()` not yet run, asyncio task not done) until a concurrent approve call
    resolves it, then resumes and returns the real result; (2) explicit human denial raises
    `BastionBlockedError` with `execute()` never invoked; (3) an unresolved approval past a
    (test-shortened, via `object.__setattr__` on the frozen `Config` singleton) TTL is discovered as
    `timed_out` and denies. Full 24-test workspace suite passes, `ruff`/`mypy --strict` clean.

## Next up
- Phase 4: trace aggregator service — subscribe to the event stream, build in-memory graphs per active
  trace, persist `trace_summaries` (read-model/projection, rebuildable from `events`) on completion.
  `GET /traces/{id}` full replay endpoint (folded event stream + graph). Milestone: pull up any past
  trace via API and get a complete, correctly-ordered causal graph as JSON.

## Known deviations from BUILD_PLAN.md
- None in phase *order*. Implementation-level deviations from the original spec docs, all flagged in
  code/API_SPEC.md/ARCHITECTURE.md rather than silently guessed: (1) interceptor language (Phase 0,
  §7), (2) interceptor doesn't proxy the real downstream call, the SDK does (Phase 1, §8), (3)
  `policy_sets` added for stable identity across policy versions (Phase 2, §10), (4) policy dashboard
  endpoints take an explicit `org_id` param until Phase 5 auth lands (Phase 2, §11), (5) `/intercept`
  never blocks for approval, `GET /approvals/{id}` is the real long-poll target (Phase 3, §13).

## Open questions / decisions needed
- None currently blocking. Three things to revisit in Phase 5: (1) `POST /agents` (dashboard API, needs
  RBAC) doesn't exist yet — tests insert agents directly via SQL as a stand-in; confirm this gets built
  in Phase 5 alongside the rest of the dashboard API. (2) Swap `/policies`' and `/approvals`' explicit
  `org_id` param/field for one derived from the authenticated JWT session (§11) — the isolation logic
  itself shouldn't need to change, only where `org_id` comes from. (3) Populate `approval_requests.
  resolved_by` once real user sessions exist; add the deferred FK to `users(id)`.
