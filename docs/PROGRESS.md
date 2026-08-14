# BASTION — Progress Log

## Status: build complete, post-launch additions in progress
All of `docs/BUILD_PLAN.md` (Phases 0-9) plus the final documentation set are done. Now working through
user-requested follow-ups: signup/registration (done), Neon Postgres connection, Render deployment, UI
polish.

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
    `UNIQUE (trace_id, sequence_number)` constraint. Full reasoning in `docs/ARCHITECTURE.md` §12.
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

- [2026-08-14] Phase 7 complete:
  - **No `frontend-design` skill available** in this environment (confirmed, not a lookup miss), and no
    tool exists that can install a Claude Code skill/plugin — that's a human-run CLI action, flagged to
    the user, who confirmed proceeding with manual design judgment instead. Documented in
    `docs/ARCHITECTURE.md` §16.
  - Vite + React 19 + TypeScript 5.9.3 (**not** 7.x — conflicts with `typescript-eslint`'s peer range)
    + react-router-dom 7 + zustand 5 + `@react-three/fiber` 9 / `@react-three/drei` 10 / `three` 0.185 +
    `d3-force-3d` (no official types, hand-written ambient `.d.ts` — `@types/d3-force-3d` doesn't exist
    on npm, confirmed 404).
  - Login page (JWT access/refresh via the Phase 5 auth API, zustand-persisted to localStorage) →
    dashboard: sidebar (connect-to-live-agent form + recent completed traces), center 3D force-directed
    graph (live WS delta-driven or historical-replay-snapshot-driven, same `GraphCanvas`/`ForceGraph`
    components either way), right-side 2D inspector panel (full span detail on node click — the actual
    debugging substance per ARCHITECTURE.md §2.6, not just the 3D view).
  - `frontend/src/api/types.ts` is a **hand-written** mirror of `bastion_shared`, not OpenAPI-generated —
    a real gap (will drift silently), not a deliberate design choice; flagged for Phase 11 in
    `docs/ARCHITECTURE.md` §16 rather than left implicit.
  - **Two rendering bugs found during first real end-to-end browser verification** (real backend, real
    Postgres, real WebSocket, real browser — not a mock), both invisible against an idle/empty graph and
    only surfacing once actual trace data flowed in:
    1. **Infinite render loop / WebGL context loss**: a zustand selector (`Array.from(store.nodes.keys())`)
       allocated a new array every call, which breaks `useSyncExternalStore`'s stability contract and
       loops until React's own depth limit throws. Fixed with `useShallow`; audited every other selector
       in the codebase for the same pattern (none found). Full root cause and fix in
       `docs/ARCHITECTURE.md` §16.
    2. **Force-simulation blowup on multi-node load**: replaying a completed trace loads its whole node
       set in one effect run (unlike a live trace, which adds nodes one at a time), and the default
       unbounded inverse-square charge force flung near-coincident nodes off-camera on the first tick.
       Fixed via `distanceMin`/`distanceMax` on the charge force plus retuned charge/center strengths.
       Also fixed, unrelated: `.graph-area` (a CSS grid `1fr` track) had no `min-width: 0`, so the
       `<Canvas>`'s intrinsic sizing could blow out the grid track before `ResizeObserver` settled,
       producing a page-level horizontal scrollbar. Both in `docs/ARCHITECTURE.md` §16.
  - **Milestone verified manually in-browser** (no automated test suite for the frontend yet — noted as
    a gap, not silently skipped): logged in as the seeded `owner` user, connected to the live agent,
    ran a real nested multi-span trace through the actual Python SDK against the actual interceptor —
    graph updated live over the WebSocket with no crash (confirming both bug fixes above), clicked a
    node and confirmed the inspector showed correct status/span_id/latency, opened historical replay for
    a completed trace and confirmed the full graph loads and settles. `npm run typecheck` (`tsc -b`) and
    `npm run lint` (`eslint .`) both clean.
  - BUILD_PLAN.md's actual Phase 7 milestone ("simulated prompt injection live, watch the blocked call
    turn red") needs Phase 8's reference demo agent to generate that scenario — not built yet, tracked
    below as before, not silently dropped.

- [2026-08-14] Phase 8 complete:
  - **LLM-vs-deterministic decision, flagged and resolved before writing any code**: BUILD_PLAN.md's
    Phase 8 implies a real LLM call (LangChain/OpenAI SDK) choosing to act on the injected instruction,
    but no LLM API key exists in this environment, and the same section's own reliability bar ("run it
    20 times, make sure it's not flaky") is much harder to guarantee with a live, nondeterministic,
    paid call in the loop. Asked the user directly; confirmed building a deterministic scripted
    "brain" instead (regex-parses the injected instruction, always acts on it) — documented as a
    substitution per CLAUDE.md rule #3, not a silent mock, in `docs/ARCHITECTURE.md` §17. Everything
    downstream of that decision (the SDK call, the interceptor, the policy engine) is completely real.
  - New workspace package `demo-agent/`: `tools.py` (fake ticket store + fake payments API, both
    explicitly documented as fake), `agent.py` (the scenario: read a ticket containing an injected
    "transfer $500 to attacker-9999" instruction, attempt it, catch the resulting
    `BastionBlockedError`, then complete a legitimate small transfer to show the policy targets the
    amount, not the tool), `seed.py` (idempotent org/agent/policy setup via direct SQL — same
    standing-in-for-a-missing-endpoint convention as `interceptor/tests/conftest.py`'s fixtures — plus
    a Redis pub/sub publish so an already-running interceptor hot-reloads the new policy with no
    restart), `run_demo.py` (CLI against a real running interceptor, with `--repeat N` for the
    reliability check).
  - Policy: `payments.transfer` blocked when `amount > 100`, active for a dedicated
    `prompt-injection-demo` agent in the existing Phase 7 demo org — chose a targeted amount-based
    block over a blanket tool block specifically so the same trace can show both a blocked call and a
    legitimate one succeeding.
  - **Milestone test passes** (`demo-agent/tests/test_scenario.py`) — both BUILD_PLAN.md's explicit
    asks, checked directly: the injected transfer is blocked while the legitimate one isn't, and the
    full scenario run 20 times in a row blocks every single time (`test_..._reliably_across_20_runs`).
    Cross-service pattern (real interceptor app via ASGITransport, real Postgres), same as Phase 4's
    milestone test. Also re-ran the same 20x check via `run_demo.py --repeat 20` against the actual
    running interceptor process (not just ASGITransport) — 20/20 blocked there too.
  - **Fulfilled BUILD_PLAN.md's actual Phase 7 milestone**, deferred at the time since the scenario
    generator didn't exist yet: connected the live dashboard to the demo agent and watched
    `payments.transfer` turn red in the 3D graph in real time as `run_demo.py` ran against the live
    interceptor, confirmed in-browser via screenshots.
  - **Real bug found during that live verification, fixed**: the live WebSocket delta path
    (`NodeUpdatedMessage`) silently dropped a blocked/failed call's `reason` — present in the replay
    path (`GraphNode.reason`, correctly folded) but never included in the live delta message, so a
    viewer watching a call get blocked *live* saw red with no explanation, while replaying the exact
    same trace afterward showed the reason correctly. Fixed across `shared/` (added the field),
    `aggregator/` (pass it through), and `frontend/` (type + store). Regression-tested
    (`aggregator/tests/test_blocked_call_delta_includes_the_block_reason`) and reconfirmed live in the
    browser after the fix. Full writeup in `docs/ARCHITECTURE.md` §17.
  - **Also fixed along the way**: `demo-agent/pyproject.toml` was missing the same
    `asyncio_default_fixture_loop_scope`/`asyncio_default_test_loop_scope = "session"` settings every
    other package's `pyproject.toml` needs (Phase 1's cross-loop asyncpg fix) — hit the identical
    "attached to a different loop" error on the very first test run, same root cause, same fix, just
    forgotten in the new package. Also added a `py.typed` marker to `sdk-python/bastion/` — the first
    time any package's *production* `src/` code (not just tests) imports the SDK as a real dependency,
    which surfaced a `mypy --strict` gap that test-only cross-package imports never had (CI only
    type-checks `src/`, not `tests/`, so this was never exercised before).
  - Full 45-test workspace suite passes (44 + the new live-WS regression test), `ruff`/`mypy --strict`
    clean across `shared/src interceptor/src aggregator/src sdk-python/bastion demo-agent/demo_agent`.
    CI workflow updated to include `demo-agent` in both the mypy and pytest invocations.
  - **Noted, not investigated further**: `interceptor/tests/test_approval_flow.py::test_approval_flow_pauses_and_resumes_on_approve`
    failed once when run as part of the full multi-package suite but passed cleanly in isolation and on
    every re-run of the full suite afterward — a pre-existing timing flake, not something this phase's
    changes caused (nothing in Phase 8 touches approvals). Consistent with the dev Postgres having
    accumulated hundreds of leftover `test-org-*` rows from every test run since Phase 0 (tests share
    the dev DB, never clean up) — worth a proper test-DB-isolation pass before Phase 9's load testing,
    where DB bloat would skew latency numbers.

- [2026-08-14] Phase 9 complete:
  - **Spec violation found and resolved, not silently**: `docs/ARCHITECTURE.md` §2.2 and `CLAUDE.md`
    rule #4 both require `/intercept`'s event writes to be fire-and-forget so the policy decision waits
    on nothing but the in-memory cache — the actual code has `await`ed every write inline since Phase 1,
    never caught until this phase's load test turned it from an abstract gap into a numbers question.
    Asked the user directly: keep synchronous writes (durability — a security audit trail that could
    silently lose the record of a call if the process crashes between "decided" and "logged" is a worse
    failure mode than extra p99 latency) or make it genuinely fire-and-forget. Confirmed: keep
    synchronous, measure and report the honest latency that results. Documented in
    `docs/ARCHITECTURE.md` §18.
  - Prometheus metrics: `intercept_latency_seconds` (histogram, wraps the whole `/intercept` handler)
    and `policy_decisions_total{decision=}` (counter) on the interceptor, per BUILD_PLAN.md's explicit
    naming; `GET /metrics` added to both interceptor and aggregator (`prometheus_client`,
    default text exposition format). Verified live: ran the Phase 8 demo scenario and confirmed
    `/metrics` showed exactly 3 allowed + 1 blocked, matching the scenario's real call count.
  - Structured logging (`structlog`, JSON logs, `request_id` correlation) was already in place since
    Phase 0 — nothing new needed here, just confirmed still true.
  - Dockerfiles for `interceptor/`, `aggregator/`, `frontend/` (multi-stage: `uv sync` for the two
    Python services since they're workspace members and need the whole workspace source tree present
    to resolve `uv.lock`, even though only one package's dependencies actually get installed per image;
    Node build → nginx static serve + reverse proxy for the frontend, mirroring `vite.config.ts`'s dev
    proxy rules exactly via `nginx.conf`). `infra/docker/docker-compose.yml` extended from
    Postgres+Redis-only into the full local stack (adds `interceptor`, `aggregator`, `frontend`, plus
    one-shot `migrate`/`generate-keys` init services) — **built and ran for real**, not just written:
    logged in through the containerized frontend, ran the Phase 8 demo scenario against the
    containerized interceptor, confirmed the blocked call end to end through every containerized
    service.
  - **Real bug found while standing up the full stack, fixed**: `demo-agent/demo_agent/seed.py`, run
    natively on the host, publishes a Redis hot-reload signal after seeding a policy — the
    *containerized* interceptor never received it. Root cause: a native, non-containerized Windows
    Redis (`redis-server.exe`) was already bound to port 6379, silently absorbing every native-host
    `redis://localhost:6379` connection (this session's own Redis usage included, though invisibly,
    since publisher and subscriber being on the same wrong server still made pub/sub work
    self-consistently until a *container* needed to receive a message from a *native* process). Fixed
    the same way §7 already fixed the equivalent Postgres collision: moved Redis's host-published port
    to 6389, updated every native-host default (`config.py` in both services, `demo-agent/seed.py`,
    `.env`/`.env.example`, README). Full writeup in `docs/ARCHITECTURE.md` §19 — this also means every
    native pytest run and native dev-mode service this whole session most likely used the wrong Redis
    the entire time, invisibly, until this phase.
  - K8s manifests (`infra/k8s/`): namespace, ConfigMap, Secret template (never real values), Deployments
    + Services for all three services, one HPA for the interceptor. **Actually deployed and verified**,
    not just written: installed `kind` (not present in this environment), created a real cluster, loaded
    the three images, applied every manifest, hit an `ImagePullBackOff` from `:latest`'s default
    `imagePullPolicy: Always` ignoring the locally-loaded image (fixed: `imagePullPolicy: IfNotPresent`
    on all three), then got every pod to `1/1 Running`, port-forwarded, and ran the Phase 8 demo
    scenario against the interceptor running inside the kind cluster — blocked correctly. Cluster torn
    down after verification. Scaling reasoning documented per-manifest: interceptor is stateless
    (rebuildable `PolicyCache`, hot-reloaded via Redis pub/sub) so scaling is just replica count;
    aggregator's per-replica Postgres `LISTEN` means every replica independently receives every
    `NOTIFY` and only needs to serve its own locally-connected WebSocket clients, so no shared fan-out
    layer is needed between aggregator replicas either.
  - **Load test, real numbers** (`infra/load-test/`, k6 via Docker — no local install needed): 50 req/s
    constant arrival rate, 30s, against a single unscaled native interceptor process. Three clean runs:
    p99 45.4ms / 46.9ms / 53.1ms (avg ~20-22ms, p95 ~37-39ms) — two of three clear
    `docs/ARCHITECTURE.md` §6's <50ms p99 target, the third misses by ~3ms, consistent with §18's
    documented synchronous-write tradeoff rather than a surprise. Two earlier exploratory runs under
    machine contention (a concurrent image pull; a dozen unrelated Docker containers already running on
    this dev machine) hit p99 as high as 562ms — reported transparently in `infra/load-test/README.md`
    as a statement about the measurement environment, not folded into the headline numbers, which come
    from three consecutive otherwise-idle-machine runs instead.
  - Full 45-test workspace suite passes, `ruff`/`mypy --strict` clean, frontend `typecheck`/`lint`
    reconfirmed clean too (no frontend code changed this phase, checked anyway).

- [2026-08-14] Final documentation set complete:
  - **Generated API docs + drift check** (`docs/api/`): pulled real `openapi.json` from both running
    services and diffed against the hand-written `docs/API_SPEC.md`. Found and fixed real drift, not
    hypothetical: (1) `API_SPEC.md` documented a `Base URL: /api/v1` prefix that was **never actually
    implemented** — every real endpoint is served bare-path, confirmed by `grep`ing both services'
    source for zero matches on `api/v1`. This had been wrong since the spec was first written and never
    reconciled against the code. (2) The WS `node_updated` example was missing the `reason` field added
    in Phase 8. (3) WebSocket routes are structurally invisible to OpenAPI (no representation for them
    in the spec format), documented as a permanent limitation of "generate docs from code" for this
    project, not something a script could ever close. Full writeup: `docs/api/DRIFT.md`.
  - **`docs/decisions.md`**: a one-file scannable index of every numbered `ARCHITECTURE.md` decision
    (§7-§19), one paragraph each. Caught one more small thing while building it: a stale cross-reference
    in this very file (Phase 1's log pointed at "§9" for the sequence-number reasoning; the actual
    section is §12) — fixed.
  - **`infra/db/seed_dev.py`**: a real gap closed, not just documented — the base demo org + owner login
    had been improvised ad hoc via inline Python twice this session (once before this log started
    tracking it in detail, once again after a `docker compose down -v` wiped it) with no reusable
    script, unlike `demo-agent/demo_agent/seed.py`'s dedicated scenario setup. `SETUP.md` needed this to
    give a genuinely copy-pasteable path from a fresh checkout to a working login, so it exists now.
  - **`SETUP.md`**: all three run modes (native dev, full Docker Compose, `kind`) as copy-pasteable
    command blocks, re-verified against the running system while writing this, not just assumed correct
    from memory of running them earlier in Phase 9. Includes a troubleshooting section covering the
    real issues actually hit this session (the Redis port collision, the per-package async loop-scope
    setting, the `kind` `ImagePullBackOff` gotcha) rather than generic advice.
  - **`CONTRIBUTING.md`**: judged relevant despite this being a solo build — restates the standing
    engineering rules from `docs/CLAUDE.md` as durable guidance for any future change, not boilerplate
    "how to submit a PR." Deliberately didn't fabricate a security-disclosure contact channel that
    doesn't exist.
  - **`README.md`**: full rewrite from the Phase 0 placeholder. Real load-test numbers front and center
    (not just a claim — the actual three-run table from `infra/load-test/README.md`), an architecture
    diagram (Mermaid — rendered and visually verified before committing, not assumed to be valid syntax),
    the PRD's "why now" pitch condensed, and the prompt-injection demo as the lead example with the
    exact commands to reproduce it. Cross-checked one factual claim before publishing: BUILD_PLAN.md has
    Phases 0 through 9 (ten phases, zero-indexed), not nine — corrected before this went in, not after.

- [2026-08-14] Signup/registration added (post-launch, user-requested):
  - `POST /auth/signup` (`interceptor/src/bastion_interceptor/main.py`): creates a brand-new org + its
    first user (role `owner`) in one transaction (`db.create_org_and_owner`), then issues a token pair
    the same way `/auth/login` does — auto-login after signup. Always a new org, no invite/join flow
    (joining an existing org via bare email+password with no invite token would be a real security
    hole). `SignupRequest` (`shared/src/bastion_shared/auth_api.py`) uses `EmailStr` (added
    `pydantic[email]` dependency) and an 8-character password minimum — no policy was specced anywhere,
    this is a reasonable default stated as such, not copied from a doc that doesn't exist.
  - Frontend: `SignupPage.tsx`, linked from `LoginPage.tsx` and back, `api.signup()` in `client.ts`,
    `/signup` route in `App.tsx`. Verified live: created a genuinely new, isolated org through the real
    UI, confirmed it landed on an empty dashboard (no cross-org data), not just that the request
    succeeded.
  - **Real bug found and fixed while testing this live, not hypothetical**: the 422 validation-error
    handler on *both* interceptor and aggregator did `str(exc.errors())`, dumping FastAPI's raw
    Python list-of-dicts repr straight into the user-facing error message — hit immediately by typing an
    invalid email into the real signup form and seeing a wall of `[{'type': 'value_error', 'loc': ...}]`
    instead of a sentence. This bug predates signup entirely (it's the generic body-validation handler,
    would have hit any endpoint with a malformed request) but had never been noticed because nothing
    before this exercised 422s through an actual UI form. Fixed in both services
    (`_format_validation_errors`, `"field: message"` joined by `"; "`), regression-tested
    (`test_validation_error_message_is_human_readable_not_a_raw_repr`), reconfirmed clean in the browser.
  - Also corrected: `shared/src/bastion_shared/__init__.py`'s module docstring claimed the frontend
    types were "generated from the FastAPI-produced OpenAPI schema" — false since Phase 7 (they're
    hand-written, documented as a real gap in §16/`docs/api/DRIFT.md`) and apparently never fixed when
    that gap was found. Fixed while in the file for an unrelated change, not a dedicated pass — worth
    noting in case other stale claims like this exist elsewhere.
  - 14 auth tests passing (11 existing + 3 new signup tests + 1 new validation-message regression test),
    `docs/API_SPEC.md` and `docs/api/*.openapi.json` updated in the same change, not left to drift again.

- [2026-08-14] Neon Postgres connected and verified (post-launch, user-requested): ran all 5 migrations
  against the real Neon instance (`infra/db/migrate.py` with `DATABASE_URL` overridden to
  `NEON_DATABASE_URL`), seeded base org/user + the Phase 8 demo agent/policy, then ran a full
  signup/login/intercept/policy-block cycle against a temporary interceptor instance pointed at Neon
  (port 4011, separate from the normal dev instance on 4001) — the exact same demo scenario from Phase 8,
  now proven against the real deployment-target Postgres, not just a connectivity check. Local dev and
  the test suite still default to Docker Compose Postgres per §7's original reasoning; Neon is wired up
  and ready for Render, not swapped in as the new local default.

## Next up
- Render deployment, UI visual polish — in progress, user-requested.
- If resumed after that: worth resetting/isolating the dev Postgres before anything latency-sensitive
  (thousands of accumulated `test-org-*` rows from every phase's test runs share the same dev DB) and
  building the CI-gated version of the API drift check `docs/api/DRIFT.md` describes but doesn't
  implement.

## Known deviations from BUILD_PLAN.md
- None in phase *order*. Implementation-level deviations from the original spec docs, all flagged in
  code/API_SPEC.md/ARCHITECTURE.md rather than silently guessed: (1) interceptor language (Phase 0,
  §7), (2) interceptor doesn't proxy the real downstream call, the SDK does (Phase 1, §8), (3)
  `policy_sets` added for stable identity across policy versions (Phase 2, §10), (4) policy dashboard
  endpoints took an explicit `org_id` param before auth existed, now removed (Phase 2 §11 → Phase 5),
  (5) `/intercept` never blocks for approval, `GET /approvals/{id}` is the real long-poll target
  (Phase 3, §13), (6) `PolicyEvaluated` event type never emitted, folded into the decision events
  instead (Phase 4, §14), (7) WS auth via query param instead of a header, browser API constraint
  (Phase 6, §15), (8) no `frontend-design` skill available, proceeded with manual design judgment
  (Phase 7, §16), (9) frontend wire types hand-written instead of OpenAPI-generated (Phase 7, §16),
  (10) demo agent's tool-selection is a deterministic scripted stand-in, not a real LLM call — no API
  key available, and reliability-tested (20x) in a way a live LLM call would undermine (Phase 8, §17),
  (11) `/intercept` event writes stay synchronous rather than fire-and-forget, a deliberate durability-
  over-latency call flagged and confirmed with the user (Phase 9, §18), (12) Redis's host-published port
  moved from the default 6379 to 6389 after discovering a real collision with a native Windows Redis on
  this dev machine, missed by Phase 0's original port-collision check (Phase 9, §19).

## Open questions / decisions needed
- None currently blocking. Things to revisit later:
  - `POST /agents` and a signup/registration endpoint still don't exist — neither AUTH.md nor
    API_SPEC.md specs one, so agents and users are both inserted directly via SQL in dev/tests. Worth
    building real endpoints for both before any actual demo that isn't purely API-driven (Phase 8's
    reference agent will need a real way to register itself).
  - Frontend has no automated test suite yet (Phase 7 was verified manually in-browser). Worth adding
    at least component/store-logic tests before Phase 9 polish, given the two real bugs manual testing
    already caught that unit tests around the zustand selectors and force-simulation setup likely would
    have too.
  - `frontend/src/api/types.ts` (hand-written) vs. FastAPI's generated OpenAPI schema: no drift check
    exists yet. Flagged for Phase 11.
