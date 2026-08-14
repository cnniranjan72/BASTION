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
- **`POST /agents` still doesn't exist** (signup now does, see below). Neither AUTH.md nor API_SPEC.md
  ever specced either; every phase's tests and seed scripts insert agents directly via SQL instead
  (documented per-fixture, e.g. `interceptor/tests/conftest.py`'s `test_agent`). Open, not forgotten —
  tracked in `docs/PROGRESS.md`'s "Open questions" section.
- **`POST /auth/signup` (post-launch, user-requested) is always a new-org signup, never a join.**
  A bare email+password join of an existing org (no invite token) would let anyone add themselves to
  any org they knew the name of — self-serve "create a new org" is the only flow that's safe without
  building invite infrastructure that was never specced. Auto-logs in after signup, same token-issuing
  path as `/auth/login`. Found a real, pre-existing bug while testing this live: both services' 422
  validation-error handler dumped a raw Python repr into user-facing error messages — fixed, not
  signup-specific but only surfaced once a real form existed to trigger it through.
