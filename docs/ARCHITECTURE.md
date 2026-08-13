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
- **Local Postgres port**: Docker Compose maps Postgres to host port **5442**, not the default 5432 — this machine already runs other local Postgres containers on 5432. Redis stays on the default 6379 (no collision found). `DATABASE_URL` in `.env.example` and both services' config defaults reflect this; the CI Postgres service container is unaffected (runs in an isolated GitHub Actions runner, so it uses 5432 with no conflict).
