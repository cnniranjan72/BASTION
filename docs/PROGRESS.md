# BASTION — Progress Log

## Status: Phase 6 complete
Phase: 6 (live WebSocket fan-out) → next up: Phase 7 (3D live frontend)

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

- [2026-08-14] Phase 4 complete:
  - **Subscription mechanism**: Postgres LISTEN/NOTIFY (ARCHITECTURE.md §2.5 offered this or "a
    lightweight queue") via a trigger-driven `NOTIFY` on every `events` insert (migration
    `0004_trace_summaries.sql`) — no extra infrastructure, and can't be forgotten by a future writer
    the way "remember to also publish to Redis" could. Minimal payload (`trace_id`/`span_id`/
    `event_type`, well under the 8000-byte `NOTIFY` limit); the aggregator re-fetches full rows itself.
  - **Trace completion detection**: no explicit "done" event exists in DATA_MODEL.md's vocabulary. The
    fold watches the *root* span (parent_span_id null) for a terminal event — reliable because the
    SDK's `call()` only reports the root's own completion after its `execute()` returns, which for the
    root means every nested call it awaited already completed (docs/ARCHITECTURE.md §8). Documented in
    `docs/ARCHITECTURE.md` §14.
  - `aggregator/src/bastion_aggregator/graph.py`: `fold_events_to_graph`, one pure function used both
    by the live LISTEN/NOTIFY handler and by `GET /traces/{id}`'s on-demand fallback — same fold, same
    code path, matching CLAUDE.md rule #1 (current state is always a fold over events, never stored
    directly as the source of truth).
  - `trace_summaries` (DATA_MODEL.md, unmodified) persisted only once a trace reaches a terminal state
    — an active trace has no row there by design; `GET /traces` only lists finished traces, while
    `GET /traces/{id}` for an in-progress one falls back to folding `events` fresh, proven by a
    dedicated test (`test_replay_before_completion_folds_live`).
  - **Noted, not a bug**: `PolicyEvaluated` (in DATA_MODEL.md's event vocabulary) is never emitted as
    its own event — `CallAllowed`/`CallBlocked`/`CallPendingApproval` already carry the policy decision
    and reasoning in their payload; a separate event would just double the count per call with no new
    information. Documented in `docs/ARCHITECTURE.md` §14.
  - **Milestone test passes** (`aggregator/tests/test_replay.py`, 4 tests) — genuinely cross-service:
    real trace data generated through the *interceptor's* app, replayed through the *aggregator's*,
    both against the same Postgres, exactly as two separately deployed services would interact.
    Verifies causal graph reconstruction (nodes/edges/status/cost), raw event listing, org-scoped
    listing, and live-fold-before-persistence. Added `bastion-interceptor`/`bastion-sdk` as aggregator
    *dev*-only dependencies (via `{ workspace = true }`) to make this cross-package test import
    explicit rather than relying on `uv sync --all-packages` installing everything incidentally.
    Full 28-test workspace suite passes, `ruff`/`mypy --strict` clean.

- [2026-08-14] Phase 5 complete:
  - `users` + `refresh_tokens` tables per DATA_MODEL.md exactly (migration `0005_users_auth.sql`),
    plus the deferred `approval_requests.resolved_by → users(id)` FK from Phase 3.
  - Ed25519 keypair (`infra/keys/generate_dev_keys.py`, idempotent, gitignored `*.pem`) — JWT access
    tokens signed with the private key (interceptor only), verified with the public key (both
    interceptor and aggregator, independently — AUTH.md's "without calling the auth service"). Access
    tokens are 15 min, stateless; no denylist/instant-revocation cache built (AUTH.md's own documented
    tradeoff, noted explicitly in `shared/src/bastion_shared/jwt_auth.py` rather than left implicit).
  - argon2id passwords (`argon2-cffi`) for humans, kept deliberately separate from agents' SHA-256 API
    key hashing (`interceptor/.../human_auth.py` vs `.../auth.py`) — AUTH.md §"two separate auth
    domains, don't conflate them."
  - **Refresh rotation + reuse detection** (`POST /auth/refresh`): every refresh token is one-time-use;
    presenting an already-revoked one (whether legitimately rotated away or previously flagged) revokes
    the *entire* family and forces re-login. `POST /auth/logout` does the same revocation on demand.
  - RBAC (`require_role`) retrofitted onto every dashboard/trace/policy/approval endpoint; org scoping
    now derives from JWT claims, replacing the Phase 2-4 explicit `org_id` param stopgap everywhere
    (`/policies`, `/approvals`, `/traces`). A cross-org resource 404s, never a distinguishable 403.
  - **Fixed a real bug found while retrofitting**: `activate_policy` was activating the target row
    *before* checking org ownership, then merely filtering the response — a cross-org caller could
    have actually flipped another org's active policy version even though they'd see a 404. Fixed by
    moving the org check into the same atomic `UPDATE ... WHERE ... AND org_id = $2` (and the
    equivalent join-based check for `resolve_approval`) — check-before-mutate, not mutate-then-hide.
  - `POST /policies/{id}/approve`/`/deny` now populate `resolved_by` from the authenticated user (was
    always `null` through Phase 3-4, documented then as pending exactly this).
  - Approver page (`GET /approvals-ui`) updated with a pasted-access-token field (no login form — that
    stays Phase 7's job) since its API calls now require real auth.
  - **Milestone test passes** (`test_refresh_token_reuse_revokes_entire_family`): simulates the actual
    theft scenario — an attacker rotates a stolen token, then the legitimate client's stale copy gets
    presented too; asserts the reuse is detected *and* that the token the attacker just legitimately
    obtained is also dead afterward, not just the stale replay rejected. 11 new auth tests total
    (login, refresh happy-path, reuse detection, logout, RBAC, missing/malformed tokens); every
    existing Phase 2-4 test that used the explicit `org_id` param was migrated to real login+JWT
    headers. Full 39-test workspace suite passes, `ruff`/`mypy --strict` clean.
  - **Also fixed**: CI's `uv sync --all-packages --dev` was missing `--all-extras`, so it never
    actually installed `pytest`/`httpx`/etc. (each package's own `[project.optional-dependencies]`) —
    this had been silently wrong since Phase 0 and would have failed CI's first real run. Added the
    missing flag plus a `generate_dev_keys.py` step.

- [2026-08-14] Phase 6 complete:
  - `WS /live/{agent_id}` (aggregator) pushes deltas straight from the same Postgres LISTEN/NOTIFY
    handler that already maintained `active_traces` since Phase 4 — no polling anywhere in the path,
    matching the milestone's own framing exactly.
  - Auth via `?token=<access token>` query param rather than a header — browsers won't let JS set a
    custom header on a WebSocket handshake. Verification logic is shared with the HTTP dependency via
    a new `human_auth.decode_bearer_token` (factored out, not duplicated); connection close uses
    application-defined codes `4401`/`4403` since a close frame has no JSON body for an error envelope.
    Documented in `docs/ARCHITECTURE.md` §15.
  - `ConnectionManager` (`aggregator/src/bastion_aggregator/ws.py`) groups sockets by `agent_id`,
    matching the endpoint shape exactly — a viewer only ever sees deltas for the one agent they
    subscribed to.
  - Delta derivation reuses the Phase 4 fold rather than a parallel implementation: on each
    notification, look up just the touched `span_id`'s node from a fresh fold and translate directly —
    `CallAttempted` → `node_added` (+`edge_added` if it has a parent), everything else →
    `node_updated`. One source of truth for "what does this event mean for the graph."
  - **Fixed along the way (test infra)**: Starlette's own `TestClient.websocket_connect` runs the ASGI
    app on a separate thread's event loop, which would hit the exact cross-loop asyncpg issue Phase 1
    already worked around (session-scoped `db` pool bound to the outer loop). Added `httpx-ws` as a
    test-only dependency instead — it drives WS through the same `httpx.AsyncClient` + `ASGITransport`
    pattern every other test uses, staying on one event loop. Documented in `docs/ARCHITECTURE.md` §15.
  - **Milestone test passes** (`test_two_viewers_see_identical_live_updates_with_no_polling`): two
    independent WebSocket connections (two simulated browser tabs, two different viewer-role users)
    subscribed to the same `agent_id`; a single real intercepted call produces `node_added` then two
    `node_updated` messages, byte-identical on both connections, received via push — no polling loop on
    either side. Plus auth tests (missing token, cross-org `agent_id`). Full 42-test workspace suite
    passes, `ruff`/`mypy --strict` clean.

## Next up
- Phase 7: the 3D live frontend (React + react-three-fiber) — **the first UI code in this project**.
  Per CLAUDE.md and BUILD_PLAN.md's own explicit ordering, this is deliberately last among the backend
  phases: Phases 1-6 all have passing milestone tests first. Use the `frontend-design` skill before
  writing any component. Force-directed layout, delta-based scene updates (never a full re-render per
  event — ARCHITECTURE.md §2.6), color/size encoding, 2D inspector panel on node click. Milestone: run
  the demo agent with a simulated prompt injection live, watch the blocked call turn red in real time.
  (Phase 8's reference demo agent — the thing that actually *produces* that live traffic — doesn't
  exist yet either; Phase 7's milestone will need at least a minimal version of it to demo against.)

## Known deviations from BUILD_PLAN.md
- None in phase *order*. Implementation-level deviations from the original spec docs, all flagged in
  code/API_SPEC.md/ARCHITECTURE.md rather than silently guessed: (1) interceptor language (Phase 0,
  §7), (2) interceptor doesn't proxy the real downstream call, the SDK does (Phase 1, §8), (3)
  `policy_sets` added for stable identity across policy versions (Phase 2, §10), (4) policy dashboard
  endpoints took an explicit `org_id` param before auth existed, now removed (Phase 2 §11 → Phase 5),
  (5) `/intercept` never blocks for approval, `GET /approvals/{id}` is the real long-poll target
  (Phase 3, §13), (6) `PolicyEvaluated` event type never emitted, folded into the decision events
  instead (Phase 4, §14), (7) WS auth via query param instead of a header, browser API constraint
  (Phase 6, §15).

## Open questions / decisions needed
- None currently blocking. One thing to revisit later: `POST /agents` and a signup/registration
  endpoint still don't exist — neither AUTH.md nor API_SPEC.md specs one, so agents and users are both
  inserted directly via SQL in dev/tests. Worth building real endpoints for both before any actual demo
  that isn't purely API-driven (Phase 8's reference agent will need a real way to register itself).
