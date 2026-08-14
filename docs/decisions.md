# Decision log

Every non-obvious design decision made across the build, in one scannable place. Each entry is a
one-paragraph summary with a pointer to the full reasoning in `docs/ARCHITECTURE.md` — this file is the
index, not a replacement for reading the section itself when the "why" actually matters for a change
you're making. Chronological by phase, which is also roughly causal order (later decisions often build
on or correct earlier ones — noted where that happens).

| §  | Phase | Decision |
|----|-------|----------|
| [7](ARCHITECTURE.md#7-language--tooling-decisions-phase-0) | 0 | Backend language pivoted from Node/TypeScript to Python + FastAPI, mid-Phase-0, before any real logic existed. Also: Postgres moved to host port 5442 (later, Redis to 6389 — see §19) to avoid colliding with other local containers. |
| [8](ARCHITECTURE.md#8-intercept-does-not-proxy-the-real-call--the-sdk-does-phase-1) | 1 | The interceptor decides + logs only; the SDK executes the real downstream call locally and reports the outcome via `POST /spans/{id}/complete`. The spec's original design (interceptor proxies the real call) had nothing to reach a downstream system with. |
| [12](ARCHITECTURE.md#12-per-trace-sequence-numbers-under-concurrent-writers-phase-1) | 1 | Per-trace `sequence_number` via a transaction-scoped Postgres advisory lock (`pg_advisory_xact_lock`), not an app-level counter — same-trace inserts serialize, different-trace inserts never contend. |
| [10](ARCHITECTURE.md#10-policy_sets-stable-identity-across-policy-versions-phase-2) | 2 | Added `policy_sets` (stable identity per policy name) so "the active policy" is always a query, never a fixed row reference — needed to reconcile "versions are immutable rows" with "hot reload changes what an agent resolves to." |
| [11](ARCHITECTURE.md#11-policy-dashboard-endpoints-are-unauthenticated-until-phase-5-phase-2) | 2 | Policy dashboard endpoints took an explicit `org_id` request field before real auth existed (Phase 5), rather than building auth early out of BUILD_PLAN.md's order or skipping multi-tenancy scoping. Superseded once Phase 5 landed. |
| [13](ARCHITECTURE.md#13-approval-flow-intercept-doesnt-block-get-approvalsid-does-phase-3) | 3 | `/intercept` never blocks for a human-timescale approval decision — it returns `pending_approval` immediately; `GET /approvals/{id}` is the real long-poll target, woken by Redis pub/sub. |
| [14](ARCHITECTURE.md#14-trace-completion-detection--event-stream-subscription-phase-4) | 4 | Trace completion is detected by watching the *root* span for a terminal event (reliable because the SDK only reports a call's own completion after everything it awaited has completed). `PolicyEvaluated` (in DATA_MODEL.md's vocabulary) is never emitted as its own event — folded into the decision events instead. |
| [15](ARCHITECTURE.md#15-websocket-auth-via-query-param-and-testing-it-phase-6) | 6 | WS auth via `?token=` query param, not a header — browsers won't let JS set one on a WebSocket handshake. Tested via `httpx-ws` to stay on one event loop (Starlette's own `TestClient.websocket_connect` runs on a separate thread's loop). |
| [16](ARCHITECTURE.md#16-frontend-no-frontend-design-skill-hand-written-types-and-two-rendering-bugs-found-in-live-verification-phase-7) | 7 | No `frontend-design` skill available in this environment (flagged, proceeded with manual design judgment); frontend wire types hand-written rather than OpenAPI-generated (real gap, addressed by Phase 11's `docs/api/` generation — see the DRIFT.md note there on why the frontend types themselves still aren't auto-generated). Two real rendering bugs found in first live browser verification: an infinite render loop from a non-memoized zustand selector, and a force-simulation blowup on multi-node replay loads. |
| [17](ARCHITECTURE.md#17-demo-agent-a-deterministic-scripted-brain-not-a-real-llm-call-phase-8) | 8 | Demo agent's tool-selection is a deterministic scripted stand-in for an LLM decision, not a real LLM call — no API key available, and BUILD_PLAN.md's own 20-run reliability bar is hard to guarantee with a live nondeterministic call in the loop. Also: a real bug found in live verification — the live WS delta path silently dropped a blocked call's `reason`, fixed and regression-tested. |
| [18](ARCHITECTURE.md#18-event-writes-on-intercept-are-synchronous-not-fire-and-forget-phase-9) | 9 | `/intercept`'s event writes stay synchronous rather than the fire-and-forget the spec calls for — a deliberate durability-over-latency tradeoff (losing the only record a call happened is worse than extra p99 latency for a security audit trail), flagged to and confirmed by the user. The load-test numbers in the README measure this real, honest latency. |
| [19](ARCHITECTURE.md#19-redis-host-port-collides-with-a-native-windows-redis-docker-compose-full-stack-phase-9) | 9 | Redis's host-published port moved from 6379 to 6389 after discovering a real collision with a native Windows Redis on the dev machine — missed by §7's original port-collision check, found while standing up the full Docker Compose stack. Corrects §7's now-false "no collision found" claim. |

## Not in the numbered list, but worth knowing

- **API_SPEC.md's `/api/v1` base URL never existed in the actual code** (`docs/api/DRIFT.md`) — a
  spec-vs-implementation drift caught during Phase 11's documentation pass, not an architecture decision
  with reasoning behind it. Fixed by correcting the doc to match the real bare-path routing.
- **`POST /auth/signup` (post-launch, user-requested) is always a new-org signup, never a join.**
  A bare email+password join of an existing org (no invite token) would let anyone add themselves to
  any org they knew the name of — self-serve "create a new org" is the only flow that's safe without
  building invite infrastructure that was never specced. Auto-logs in after signup, same token-issuing
  path as `/auth/login`. Found a real, pre-existing bug while testing this live: both services' 422
  validation-error handler dumped a raw Python repr into user-facing error messages — fixed, not
  signup-specific but only surfaced once a real form existed to trigger it through.
- **The frontend was a viewer, not an operator console, until a post-launch review caught it** — the
  backend had full agent/policy/approval management from Phase 2-3 onward, but the only way to actually
  *use* any of it was direct API calls; a brand-new signup landed on a live-graph screen with no agents,
  no traces, and no path forward. Closed by building `POST`/`GET /agents` + `PATCH /agents/{id}` (never
  existed before this — the last real gap in the "everything only via SQL" list) and three new pages
  (Agents, Policies, Approvals) plus nav between them and the graph view, with a real onboarding
  empty-state instead of "enter an agent_id and connect." Every piece verified against the real running
  app, not just written and assumed correct: created a genuinely new org via signup, created an agent
  through the UI and confirmed its revealed key actually authenticates, created and activated a policy,
  and drove a real `require_approval` call through to a UI-clicked Approve.
- **Team provisioning, not email invites.** `POST /users` (post-launch, user-requested — "implement
  RBAC") lets an owner/admin directly create a teammate's account with a role and a one-time-revealed
  temporary password, rather than sending an invite email. No email-sending infrastructure exists
  anywhere in this project (never specced, never built) — building a fake "invite sent" flow that
  doesn't actually deliver anything would be exactly the kind of silent mock CLAUDE.md rule #3
  prohibits. Demoting the organization's last owner is blocked (409 `LAST_OWNER`) because it's a
  self-inflicted lockout — nobody left who could activate a policy, provision anyone, or undo the
  mistake — not blocked as "demotion" generally; promoting someone else to owner first, then demoting
  the original, still works.
- **Traces/Analytics/command palette (post-launch, user-requested — "add more tabs features... loaded
  yet meaningful product") stayed frontend-only, on purpose.** All three are built entirely from
  `GET /traces`/`GET /agents`/`GET /approvals`, which already existed — no new backend endpoints, no new
  aggregation service. Analytics' charts are three small hand-rolled SVG components rather than a
  charting library dependency, since the data volumes involved (a trace list) never approach where a
  library's bundle cost or virtualization would earn its keep. Keeping the addition to three pieces
  instead of a longer list was itself a scope decision — the request explicitly asked for "meaningful,"
  not maximal.
- **Overview became the new "/" landing page, Graph moved to "/graph".** The product previously opened
  straight into the 3D live graph, which is compelling once you have an agent running but is a dead end
  for literally everyone's first session (zero agents, zero traces, nothing to look at). An at-a-glance
  home page — agent/policy/approval counts, recent traces, an explicit "create an agent → write a
  policy → watch it live" checklist when the org is empty — replaces that with something that actually
  orients a new user, composed entirely from existing list endpoints rather than new backend
  aggregation surface.
