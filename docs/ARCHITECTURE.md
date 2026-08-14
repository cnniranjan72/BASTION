# BASTION — System Architecture

## 1. High-level components

```
┌─────────────┐      ┌──────────────────┐      ┌─────────────────┐
│  Agent Code │──1──▶│  BASTION SDK       │──2──▶│  Interceptor     │
│ (any stack) │      │ (thin client lib) │      │  (proxy service) │
└─────────────┘      └──────────────────┘      └────────┬─────────┘
                                                          │3
                                    ┌─────────────────────┼─────────────────────┐
                                    ▼                     ▼                     ▼
                          ┌──────────────┐      ┌──────────────────┐  ┌──────────────┐
                          │ Policy Engine │      │  Event Store      │  │ Queue (async) │
                          │ (in-memory +  │      │  (append-only,    │  │ approvals,    │
                          │  hot-reload)  │      │  Postgres/Kafka)  │  │ notifications │
                          └──────────────┘      └────────┬──────────┘  └──────────────┘
                                                          │4
                                                          ▼
                                                ┌──────────────────┐
                                                │ Trace Aggregator  │
                                                │ (builds graph      │
                                                │  from events)      │
                                                └────────┬──────────┘
                                                          │5 (WebSocket/SSE)
                                                          ▼
                                                ┌──────────────────┐
                                                │  3D Live Frontend │
                                                │  (Three.js/R3F)   │
                                                └──────────────────┘
```

Flow: (1) agent wants to call a tool → (2) SDK wraps the call, sends it to the interceptor instead of calling directly → (3) interceptor evaluates policy synchronously, writes an event, and either forwards the real call, blocks it, or parks it for human approval → (4) every event is written to the append-only store and picked up by the aggregator → (5) the aggregator pushes live graph deltas to any connected frontend.

## 2. Component detail

### 2.1 BASTION SDK (client library)
- Thin wrapper: `BASTION.call(tool_name, payload, context)` replaces a direct API/DB call.
- Injects `trace_id` (new if root call, inherited if nested) and `span_id` (new per call, `parent_span_id` set).
- Language: start with Python (most agent frameworks are Python). Design the wire protocol so a JS SDK is trivial later.
- Fails safe: if the interceptor is unreachable, SDK behavior is configurable — fail-open (allow, log warning) or fail-closed (block) — this is a real production design decision, document your reasoning.

### 2.2 Interceptor (proxy service)
- Stateless, horizontally scalable service (this is your load-balancing/scaling story).
- On each call: (a) look up policy for `(agent_id, tool_name)` from in-memory cache, (b) evaluate, (c) emit `CallAttempted` event, (d) if allowed, execute the real downstream call (HTTP passthrough or DB passthrough) and emit `CallCompleted`/`CallFailed`, (e) if blocked, emit `CallBlocked` and return an error to the SDK, (f) if requires approval, emit `CallPendingApproval`, publish to the approval queue, and long-poll/webhook-wait for a decision (with timeout → default deny).
- This is the latency-critical path — target <50ms overhead p99. In-memory policy cache avoids a DB hit on the hot path; events are written async (fire-and-forget to the queue, not blocking the response) except for the decision itself.

### 2.3 Policy Engine
- Policies are declarative YAML/DSL, compiled into an in-memory decision tree per `(agent_id, tool_name)` pattern.
- Example policy shape:
```yaml
- match:
    tool: "db.query"
    pattern: "^DELETE"
    database: "production"
  action: block
- match:
    tool: "payments.charge"
  action: require_approval
  condition: "amount > 50"
- match:
    tool: "*"
  action: allow  # default
```
- Hot reload: policy changes pushed via pub/sub (Redis pub/sub or Postgres LISTEN/NOTIFY) to all interceptor instances, no redeploy needed.

### 2.4 Event Store (event sourcing core)
- Append-only. Every event is immutable: `CallAttempted`, `PolicyEvaluated`, `CallAllowed`, `CallBlocked`, `CallPendingApproval`, `ApprovalGranted`, `ApprovalDenied`, `CallCompleted`, `CallFailed`.
- Schema: `event_id, trace_id, span_id, parent_span_id, agent_id, event_type, payload (jsonb), timestamp, sequence_number`.
- Storage: Postgres for v1 (simpler, still demonstrates the pattern correctly); document how you'd migrate to Kafka + a materialized view store at higher scale — this is a great "how would this scale 100x" interview answer to have ready.
- The "current state" of a trace is never stored directly — it's always derived by folding events in order. This is the actual event-sourcing discipline; don't cheat by also maintaining a mutable "trace status" row as source of truth (a denormalized read-model cache is fine, as long as it's clearly a projection, not the source of truth).

### 2.5 Trace Aggregator
- Subscribes to the event stream (Postgres LISTEN/NOTIFY or a lightweight queue).
- Builds/updates an in-memory graph per active trace: nodes = spans, edges = parent/child + temporal ordering.
- Pushes incremental graph deltas (`node_added`, `node_updated`, `edge_added`) over WebSocket to subscribed frontend clients.
- On trace completion, the full graph is persisted as a read-model for fast replay later (avoids re-folding thousands of events every time someone views an old trace).

### 2.6 3D Live Frontend
- React + react-three-fiber (Three.js) for the live execution graph.
- Force-directed layout (d3-force-3d or a custom spring simulation) — nodes repel, edges pull, so the graph organizes itself live as the agent runs.
- Visual encoding: node color = status (grey=pending, green=allowed, red=blocked, amber=awaiting approval), node size = cost/latency, edge animation = causal flow direction.
- Must stay smooth under load — this means the frontend does NOT re-render the whole graph on every event; it applies deltas to a persistent Three.js scene graph. This is a real engineering constraint worth being able to explain.
- Secondary 2D "trace inspector" panel (click a node → see full payload, timing, policy decision reasoning) — the 3D view is the "wow," the 2D panel is where actual debugging happens.

## 3. Data model (see DATA_MODEL.md for full schema)
Core entities: `Agent`, `Policy`, `Trace`, `Span` (event-sourced, derived), `ApprovalRequest`, `User` (for approvers/auth).

## 4. Auth & multi-tenancy
- Every agent registers with an API key (`agent_id` + secret) — this is how the interceptor knows which policy set applies.
- Human users (the ones approving/denying, viewing dashboards) authenticate via standard session + JWT, refresh token rotation (this is your "Auth" depth requirement, done properly, not just decoration — see AUTH.md).
- Multi-tenant from day one: every table scoped by `org_id`; policies, traces, and agents are never visible cross-org. This is a real production concern (not a fresher afterthought).

## 5. Deployment
- Docker Compose for local dev; Kubernetes manifests for "production" deploy (even if you only ever run it on a single node — the manifests + reasoning are what matter for the interview story).
- Interceptor and Aggregator are separately scalable services (document why: interceptor scales with agent traffic, aggregator scales with number of live dashboard viewers — different scaling dimensions, a real system design point).

## 6. Non-functional targets (write these down, measure them, be able to defend them)
- Interceptor p99 added latency: <50ms
- 3D frontend: stable at 60fps with graphs up to ~500 live nodes
- Event store: no data loss on interceptor crash mid-call (document the exactly-once/at-least-once tradeoff you chose and why)
- Horizontal scale: interceptor instances stateless and scale linearly (load test this, show the numbers)

## 7. Language & tooling decisions (Phase 0)

This doc originally left the interceptor/aggregator implementation language open. Decided during Phase 0 scaffolding:

- **Interceptor + aggregator: Python + FastAPI** (not Node/Fastify, the initial Phase 0 pick, reverted before any real logic was written). Reasoning: this makes the entire non-UI backend one language — SDK (§2.1, always Python per this doc), interceptor, aggregator, and the Phase 8 reference demo agent are all Python — which matches this doc's own observation that "most agent frameworks are Python." `uvicorn`/`asyncio` comfortably clears the <50ms p99 interception target at this project's scale.
- **Shared schema, revised mechanism**: `shared/` (package `bastion_shared`) is Pydantic models, not Zod/TypeScript. `interceptor`, `aggregator`, and `sdk-python` all *import the same Pydantic classes directly* — this is a stronger drift guarantee than the originally-planned generated-JSON-Schema approach, since there's only one runtime representation of the event/policy/intercept shape, not two kept in sync. The frontend (`frontend/`, TypeScript, built in Phase 7) is the one place a second representation is unavoidable — its types will be generated from the OpenAPI schema FastAPI produces automatically, rather than hand-maintained. This also gives the Phase 11 "flag drift between API_SPEC.md and code" deliverable a concrete artifact to diff against (the generated OpenAPI JSON), instead of a manual comparison.
- **Python monorepo tooling**: `uv` workspace (root `pyproject.toml`, members `shared`/`interceptor`/`aggregator`/`sdk-python`) — one lockfile, one venv, editable cross-package installs. `ruff` for lint+format, `mypy --strict` for type-checking, `pytest` for tests, all run through `uv run`.
- **Local Postgres port**: Docker Compose maps Postgres to host port **5442**, not the default 5432 — this machine already runs other local Postgres containers on 5432. `DATABASE_URL` in `.env.example` and both services' config defaults reflect this; the CI Postgres service container is unaffected (runs in an isolated GitHub Actions runner, so it uses 5432 with no conflict). ~~Redis stays on the default 6379 (no collision found).~~ **Correction, Phase 9**: this turned out to be wrong — see §19.
- **Deployment targets (Phase 9, provisioned early)**: Neon (managed Postgres) and Render (hosting, via `render` CLI) credentials were provided and verified during Phase 1, stored in the gitignored root `.env` (never committed). Local dev and the test suite still run against Docker Compose Postgres, not Neon — Neon is reserved for the actual deployed environment once Phase 9 is reached, so the fast/free/isolated local loop (in particular the concurrency test in §8) isn't paying network latency or shared-instance contention on every run.

## 8. `/intercept` does not proxy the real call — the SDK does (Phase 1)

§2.2 step (d) above describes the interceptor itself "execut[ing] the real downstream call (HTTP passthrough or DB passthrough)" when a call is allowed. In practice, neither `InterceptRequest` (API_SPEC.md) nor DATA_MODEL.md gives the interceptor anything to reach a downstream system *with* — no target URL, no DSN, no per-tool adapter registry, and no endpoint to register one. Building a generic "call anything" proxy without that configuration isn't well-specified, and folding the real call's latency into the interceptor's response would also break the <50ms p99 target (§6): that number only means something if it measures *decision* latency, separate from however long the real payments API or DB query takes.

**Decision**: the interceptor decides and logs; it never executes the real call. `POST /intercept` returns `allowed`/`blocked`/`pending_approval` only (`result` is always `null` for now). The Python SDK (`sdk-python`), after receiving `allowed`, invokes a caller-supplied `execute` callback to perform the real action itself — and *only* invokes it on `allowed`, which is what actually stops a blocked call from running (not just advises against it — PRD.md §5.1's "no prevention" gap). The SDK then reports the outcome via a new endpoint, `POST /spans/{span_id}/complete` (documented in API_SPEC.md, not in the original spec — added here), which emits `CallCompleted`/`CallFailed` so the event log stays complete without the interceptor ever touching the downstream system.

This also shaped the SDK's trace/span propagation: rather than requiring callers to thread `trace_id`/`parent_span_id` through by hand, `sdk-python`'s `BASTION.call()` tracks the current span in a `contextvar`, so a nested `call()` invoked from inside an `execute` callback automatically inherits the right parent — and concurrent children (`asyncio.gather`) each get an independent copy of that context, which is what makes the Phase 1 milestone test's concurrent-nested-calls causality check possible without manual bookkeeping.

## 10. `policy_sets`: stable identity across policy versions (Phase 2)

Spec gap, flagged rather than silently resolved: DATA_MODEL.md has
`agents.default_policy_set_id` as a single FK, `policies` versioned as new
rows ("never edited in place"), and BUILD_PLAN.md's Phase 2 milestone
requires a policy change via the API to change running interceptor behavior
with no restart. Taken together these don't fit — if `default_policy_set_id`
pointed straight at one `policies.id` (a specific version), activating a new
version would never change what an agent resolves to; every agent referencing
the old version's id would need to be manually repointed, which contradicts
"no restart, no manual intervention" hot reload.

**Resolution**: added `policy_sets` (org_id, name, unique per org) — a stable
identity for a policy *name* across all its versions. `policies.policy_set_id`
and `agents.default_policy_set_id` both reference `policy_sets(id)`, not each
other. "The active policy for this agent" is always a query — the row in
`policies` where `policy_set_id = agent.default_policy_set_id AND active` —
never a fixed reference to one version. A partial unique index
(`policies(policy_set_id) WHERE active`) makes "at most one active version
per set" a DB guarantee, not just application discipline. Documented in
`docs/DATA_MODEL.md` under `policy_sets`.

## 11. Policy dashboard endpoints are unauthenticated until Phase 5 (Phase 2)

`GET/POST /policies` and `POST /policies/{id}/activate` are part of API_SPEC.md's
"Human/dashboard API," which BUILD_PLAN.md Phase 5 explicitly describes as
being *retrofitted* with JWT auth + RBAC ("every dashboard/trace/policy
endpoint now requires auth + org scoping"). Building real auth now would mean
skipping ahead of Phase 3/4 in BUILD_PLAN's order for a concern the plan
already schedules — so for Phase 2, these endpoints take an explicit `org_id`
request field/param instead of deriving it from a session, with a code
comment at each call site. This is a deliberate, temporary, and loud gap
(not a silent one): the multi-tenancy isolation test CLAUDE.md rule #7
requires ("prove org A cannot read org B's data") is written now, against
this explicit-`org_id` shape — Phase 5 only changes *where* `org_id` comes
from (JWT claims instead of a request field), not the isolation logic itself.

## 12. Per-trace sequence numbers under concurrent writers (Phase 1)

DATA_MODEL.md requires `events.sequence_number` to be "strictly increasing per trace." Under concurrent nested calls on the same trace (the exact scenario the Phase 1 milestone test exercises), naively computing `MAX(sequence_number) + 1` and inserting is a race. The fix, in migration `0001_init.sql`: a Postgres function `bastion_next_sequence_number(trace_id)` takes a **transaction-scoped advisory lock** keyed on `hashtextextended(trace_id::text, 0)` (a 64-bit hash, matching `pg_advisory_xact_lock(bigint)` directly) before computing the next value. Concurrent inserts on the *same* trace serialize against each other; inserts on *different* traces essentially never contend (different hash values), so unrelated traces don't block each other. The lock releases automatically on commit/rollback. A `UNIQUE (trace_id, sequence_number)` constraint is a hard backstop in case that reasoning is ever wrong.

## 14. Trace completion detection + event-stream subscription (Phase 4)

**Subscription mechanism**: §2.5 offers a choice ("Postgres LISTEN/NOTIFY or a lightweight queue"). Went with LISTEN/NOTIFY: `events` already lives in Postgres, so a trigger-driven `NOTIFY` on every insert (migration `0004_trace_summaries.sql`) needs no extra infrastructure and can't be forgotten by a future writer the way "remember to also publish to Redis after every insert" could. The notification payload is deliberately minimal (`trace_id`, `span_id`, `event_type` — well under `NOTIFY`'s 8000-byte limit even for a large tool-call payload); the aggregator re-fetches full rows itself rather than trusting an unbounded payload. Delivery is at-least-once (a dropped/reconnected LISTEN connection can miss a notification); the handler always re-fetches and re-folds the *whole* trace rather than applying an incremental diff, so processing the same `trace_id` twice — or recovering after a missed one, once the *next* event on that trace arrives — is idempotent by construction. `GET /traces/{id}` never depends on the aggregator having seen every notification either: it falls back to folding `events` fresh if no `trace_summaries` row and no in-memory entry exist.

**Trace completion**: a trace has no explicit "done" event in DATA_MODEL.md's vocabulary. The fold (`aggregator/src/bastion_aggregator/graph.py`) instead watches the **root span** (the one whose `CallAttempted` has `parent_span_id = null`) for a terminal event — `CallCompleted`, `CallFailed`, `CallBlocked`, or `ApprovalDenied`. This is reliable because of how the SDK's `call()` is built (§8): a root call's `execute()` callback is exactly the code that fires any nested calls, and the SDK only reports the root's own completion *after* `execute()` returns — which for the root means after every nested call it awaited has already completed. So "root reached a terminal state" is never observed before its descendants have. `trace_summaries` is only written once a trace reaches this terminal state (an active trace has no row there by design — `GET /traces` intentionally only lists finished traces; `GET /traces/{id}` for an in-progress one folds live).

**`PolicyEvaluated` is never emitted as its own event**: DATA_MODEL.md's event vocabulary lists it alongside `CallAllowed`/`CallBlocked`, but the interceptor's policy decision *is* `CallAllowed`/`CallBlocked`/`CallPendingApproval` — those already carry `policy_id` and reasoning in their payload (`PolicyDecisionPayload`). A separate `PolicyEvaluated` event immediately before the outcome event would record no additional information, just double the event count per call. Simplification, not an oversight — noted here since the fold's `_STATUS_FOR_EVENT_TYPE` table only maps the event types actually emitted.

## 13. Approval flow: `/intercept` doesn't block, `GET /approvals/{id}` does (Phase 3)

Spec tension, flagged and resolved: ARCHITECTURE.md §2.2 step (f) describes the interceptor itself doing the "long-poll/webhook-wait for a decision" when a call requires approval. But API_SPEC.md *also* defines a separate `GET /approvals/{id}` whose own doc comment says "SDK long-polls this" — and a human approval can take minutes, which `/intercept` holding a connection open for would contradict "stateless, horizontally scalable" (§2.2) and load-balancer-friendly request handling.

**Decision**: `POST /intercept` returns `pending_approval` immediately — it creates the `approval_requests` row and emits `CallPendingApproval`, but never blocks past normal request latency. `GET /approvals/{id}` is the actual long-poll: it blocks up to `APPROVAL_LONG_POLL_SECONDS` (default 25s, `interceptor/src/bastion_interceptor/config.py`), woken early by a Redis pub/sub signal (`redis_bus.wait_for_approval_signal`/`publish_approval_resolved`) rather than busy-polling Postgres — but Postgres is re-checked as the source of truth regardless of whether the wake-up signal arrived (a signal published before the waiter subscribes is simply missed, same as any pub/sub system; the mandatory re-check after every wait makes this a latency cost in the unlucky case, never a correctness bug). The Python SDK (`sdk-python/bastion/client.py`) calls `GET /approvals/{id}` in a loop until the status leaves `pending` or its own overall budget (`approval_max_wait`, default 60s) elapses, at which point it fails closed (`BastionBlockedError`) — same prevention guarantee as a straight policy block, `execute()` is never invoked.

**Timeout mechanism**: an approval has an absolute deadline (`APPROVAL_TTL_SECONDS`, default 300s) past `requested_at`. Rather than a background sweeper process, the deadline is checked *lazily* — each `GET /approvals/{id}` call, after its long-poll wait, atomically flips the row to `timed_out` if it's still `pending` and past the deadline (`db.expire_stale_approval`, guarded by `WHERE status = 'pending'` so only one caller ever wins the transition and emits the `ApprovalDenied` event for it). This keeps the timeout logic co-located with the code that already needs to re-check status, at the cost of a timed-out approval not visibly transitioning until *something* next polls it — acceptable since the SDK is always polling while a call is actually waiting.

**Event modeling**: an approval resolved via this flow never gets its own `CallAllowed`/`CallBlocked` event — `ApprovalGranted`/`ApprovalDenied` *is* that decision. `db.get_span_decision` (used by `POST /spans/{id}/complete`, which requires an allowed span) treats `ApprovalGranted` as the allowed-equivalent, `ApprovalDenied` as the blocked-equivalent, rather than the interceptor emitting a redundant second event.

## 15. WebSocket auth via query param, and testing it (Phase 6)

**Auth**: `WS /live/{agent_id}` needs the same JWT auth as every other dashboard endpoint, but a browser's WebSocket API doesn't let JS set an `Authorization` header on the handshake request. The token travels as `?token=...` instead, verified via a new `human_auth.decode_bearer_token` helper shared with (factored out of) the Header-based HTTP dependency — same verification logic, two different places to pull the raw token from. Connection is closed with application-defined codes `4401`/`4403` (missing/invalid token, cross-org `agent_id`) rather than a JSON error body — a WebSocket close frame doesn't carry one the way an HTTP response does.

**Delta computation**: rather than diffing successive full-graph folds, `_handle_notification` (main.py) looks up just the one node the incoming event touched (via the `span_id` already in the `NOTIFY` payload) from the freshly-folded graph, and derives the message directly: `CallAttempted` → `node_added` (+ `edge_added` if it has a parent), anything else → `node_updated`. This reuses the exact fold `GET /traces/{id}` and the Phase 4 `active_traces` tracking already depend on — one source of truth for "what does this event mean for the graph," not a second parallel implementation for the live-delta path.

**Testing**: Starlette's own `TestClient.websocket_connect` runs the ASGI app on a separate background thread with its own event loop — which would hit the exact cross-loop asyncpg error Phase 1 already worked around (`asyncio_default_fixture_loop_scope`, root `pyproject.toml`), since the session-scoped `db` pool is bound to the *outer* loop. Used `httpx-ws` instead (`aggregator/tests/test_live_ws.py`), which drives the WebSocket handshake through the same `httpx.AsyncClient` + `ASGITransport` pattern every other test already uses, staying on one event loop throughout.

## 16. Frontend: no `frontend-design` skill, hand-written types, and two rendering bugs found in live verification (Phase 7)

**No `frontend-design` skill available**: CLAUDE.md calls for using a `frontend-design` skill before writing any React/Three.js UI code. No such skill exists in this environment (confirmed, not a lookup miss) and there is no tool available that can install a Claude Code skill/plugin — that's a human-run CLI action. Flagged to the user directly; proceeded with manual design judgment instead: a dark low-key canvas background so emissive node colors (status-encoded: green completed, red blocked, blue in-flight) read as the focal point, a plain-table 2D inspector for the actual debugging substance per §2.6, force-directed layout via `d3-force-3d`.

**`bastion_shared` not reused for the frontend wire types**: `frontend/src/api/types.ts` is a hand-written TypeScript mirror of the Pydantic models in `bastion_shared`, not generated from FastAPI's OpenAPI schema even though Phase 0 (§7) specifically picked FastAPI in part for that schema. Codegen was skipped for Phase 7 to keep scope bounded; the two are currently in sync by hand-checking against `API_SPEC.md` and the real response shapes hit during browser verification, but they **will** drift silently the next time a backend model changes. Real gap, not a design choice — Phase 11 (final docs, "flag API_SPEC.md drift") should either add OpenAPI-generated types or an explicit drift check, not just prose documentation.

**`d3-force-3d` has no official types**, and no `@types/d3-force-3d` package exists on npm (confirmed 404) — `frontend/src/types/d3-force-3d.d.ts` is a hand-written ambient module covering only the API surface `ForceGraph.tsx` actually calls.

**Two rendering bugs found during first real end-to-end browser verification** (both were invisible with an empty/idle graph and only surfaced once real trace data flowed through the live WebSocket or a replay snapshot):

1. **Infinite render loop** (`ForceGraph.tsx`): the `nodeIds` selector read `Array.from(store.nodes.keys())`, allocating a new array on every call. Zustand v5 is built on `useSyncExternalStore`, which requires a selector to return a referentially stable result when the underlying data hasn't changed; a selector that never does causes React to see a "changed" snapshot on every render, looping until React's own depth limit throws ("Maximum update depth exceeded") and the WebGL context is lost. Fixed by wrapping the selector in `useShallow` from `zustand/react/shallow`. Audited every other store selector in the frontend for the same pattern (`grep` across `src/`) — no other instance found; all others read primitives, store-defined action functions, or `Map.get()` directly, all of which are stable unless the underlying value genuinely changed.
2. **Force-simulation blowup on multi-node load** (`ForceGraph.tsx`): a live trace adds one node at a time, so this never showed up in ad hoc testing, but replaying a completed trace loads its whole node set in a single effect run. All nodes spawn in a small radius-0.5 shell near the origin; `forceManyBody`'s inverse-square repulsion has no floor by default, so near-coincident nodes produce an enormous first-tick force that flings them off-camera before the (weak, 0.05-strength) centering force can recover — visually indistinguishable from the graph being empty. Fixed by adding `distanceMin`/`distanceMax` to the ambient `d3-force-3d` types and the charge force (caps both the close-range singularity and how far an established layout can drift), lowering charge strength (-18 → -12), and raising centering strength (0.05 → 0.15) so the whole graph converges within the default camera framing instead of requiring the user to manually zoom out via `OrbitControls` first.

Separately (not a force-simulation issue): `.graph-area`, the `1fr` track in `.dashboard__body`'s CSS grid, had no `min-width: 0`. Grid tracks default to `min-width: auto`, so the `<Canvas>` element's own intrinsic sizing could push the track wider than the viewport before its `ResizeObserver`-based sizing settled, producing a page-level horizontal scrollbar and visually shifting the rendered scene off past the right edge. Fixed with `min-width: 0; overflow: hidden` on `.graph-area` — the standard fix for CSS Grid's sizing-blowout behavior with intrinsically-sized children.

## 17. Demo agent: a deterministic scripted "brain," not a real LLM call (Phase 8)

BUILD_PLAN.md's Phase 8 describes the demo agent as built with "LangChain or raw OpenAI SDK" — implying it makes a real LLM call to decide which tools to invoke, including being steered by the injected instruction in the document it reads. Two things are in tension with that: no LLM API key was available in this environment, and BUILD_PLAN.md's own reliability bar for this exact scenario ("run it 20 times, make sure it's not flaky") is much harder to guarantee with a live, nondeterministic, paid API call in the loop — a real LLM might simply not fall for the injection on a given run, which would make the "reliability" test flaky by the nature of what it's testing, not because of a bug.

Flagged to the user rather than guessed; decision (confirmed): `demo-agent/demo_agent/agent.py`'s tool-selection logic is a deterministic function — it reads the fake ticket, regex-parses the injected instruction (`tools.parse_injected_transfer`), and always attempts the transfer it finds. This is a stand-in for an LLM's decision, not a prompt-injection detector and not a claim that this is what a real LLM would do every time. It is documented as a substitution in both `agent.py`'s docstring and `tools.py`'s, per CLAUDE.md rule #3 ("no mock data pretending to be real integrations... say so explicitly") — the thing that's real and actually under test is everything downstream of that decision: the real `BastionClient`, the real interceptor, the real policy engine actually blocking the call. `demo-agent/tests/test_scenario.py` asserts the 20-run reliability bar directly against that real path.

**Bug found while verifying this scenario live in the browser** (not caught by any existing test, since `aggregator/tests/test_live_ws.py`'s only blocked-call coverage came later, added specifically because of this): the live WebSocket delta path silently dropped a blocked/failed call's `reason`. `fold_events_to_graph` (§14) correctly populates `GraphNode.reason` from the event payload, and `GET /traces/{id}` (replay) returns it — but `_handle_notification`'s `NodeUpdatedMessage` (aggregator `main.py`) only ever carried `status`/`latency_ms`/`cost`, never `reason`, so a viewer watching a call get blocked *live* saw the node turn red with no explanation, while the same trace viewed via replay afterward showed the reason correctly. Fixed by adding `reason` to `NodeUpdatedMessage` (`shared/src/bastion_shared/realtime.py`) and passing `node.reason` through in the aggregator; mirrored in the frontend's hand-written `LiveMessage` type and the graph store's delta-application logic. Regression-tested (`aggregator/tests/test_blocked_call_delta_includes_the_block_reason`) and confirmed live in-browser against the real running stack.

## 18. Event writes on `/intercept` are synchronous, not fire-and-forget (Phase 9)

§2.2 above and `docs/CLAUDE.md` rule #4 both say event writes on the latency-critical path should be fire-and-forget/async, with the policy decision itself waiting on nothing but the in-memory policy cache. The actual code (`POST /intercept`, `main.py`) has never matched that: every `CallAttempted`/`CallAllowed`/`CallBlocked`/`CallPendingApproval` write is `await`ed inline, including `CallAttempted` before the policy decision even runs — true since Phase 1, never flagged until Phase 9's load test made it a numbers question rather than an abstract one.

Flagged to the user rather than silently fixed or silently reported against: the honest tradeoff is that a security control plane telling a caller "allowed" (or "blocked") before that decision is durably recorded risks losing the only record the call ever happened, if the process crashes in the gap — for a product whose entire value proposition is "every tool call is intercepted and audited," that's arguably a worse failure mode than a few extra milliseconds of p99 latency. Decision (confirmed): keep the writes synchronous. The load-test numbers in README.md measure this real, honest latency — not the lower number a fire-and-forget version would produce — and this section is the flagged, reasoned deviation from §2.2 / CLAUDE.md rule #4 rather than a silent gap.

## 19. Redis host port collides with a native Windows Redis, docker-compose full stack (Phase 9)

§7's Phase 0 note said Redis could stay on its default port 6379 with "no collision found" against other local Postgres/Redis containers on this machine. That check missed a *native*, non-containerized Redis (`redis-server.exe`, installed under `C:\Program Files\Redis`, running as an ordinary Windows process) already bound to 6379. Windows/Docker Desktop's networking allows both to "listen" on 6379 without an explicit bind error, because they sit in different network namespaces (the native Windows process vs. Docker Desktop's WSL2/vpnkit port-forwarding) — but which one actually receives a plain `localhost:6379` connection from a native host process depends on host routing, not on which container you intended.

Found while standing up the full docker-compose stack (interceptor/aggregator/frontend, this phase): `demo-agent/demo_agent/seed.py`, run natively on the host, publishes a Redis pub/sub hot-reload signal after seeding a policy — the *containerized* interceptor never received it, silently leaving its policy cache empty and the demo's injected transfer going through unblocked instead of getting caught. `docker exec bastion-redis redis-cli publish ...` reached the container's subscriber immediately; the native seed script's `redis://localhost:6379` did not — conclusively pointing at two different physical Redis servers behind the same host/port pair.

This wasn't a one-off for this phase either: every native (non-containerized) interceptor/aggregator process and every native pytest run this session used the same `redis://localhost:6379` default, meaning they were most likely all talking to the native Windows Redis, not `bastion-redis`, the whole time — invisibly consistent (publisher and subscriber both native, so pub/sub still worked end-to-end within a single test/process) until a *container* needed to receive a message from a *native* process, which is exactly what surfaced it.

Fixed the same way §7 already fixed the analogous Postgres collision: moved Redis's **host-published** port to **6389** (`infra/docker/docker-compose.yml`) — the container-internal port and inter-container traffic, e.g. `redis://redis:6379` between the compose `interceptor`/`aggregator` services, are untouched, since that's Docker's own DNS-resolved network, not the host's. Updated every native-host default that pointed at 6379: both services' `config.py`, `demo-agent/demo_agent/seed.py`, `.env`/`.env.example`, and README.md's quickstart comment.
