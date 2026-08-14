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
- **Usability pass driven by direct user testing (post-launch, user-requested — "I can understand
  nothing from it," "what does had_blocks mean," "the layout uses only 75% of the screen").** Real
  defects found by actually looking at the running product, not assumed:
  - `.page`/`.page--wide` capped content at 1100px/1300px with no centering, leaving a large dead gap
    on the right at real monitor widths — raised to 1600px/1900px with `margin: 0 auto`.
  - Every status was shown as its raw backend enum (`had_blocks`, `pending_approval`) with no
    explanation. Added `lib/labels.ts` (plain-English label + one-line description per status) used
    everywhere a status renders, plus a legend on the Traces page and a color legend overlaid on the
    3D graph itself.
  - The 3D graph had no text anywhere — colored spheres only made sense to someone who already knew the
    code. Added an always-visible tool-name label under every node (`@react-three/drei`'s `Html`) and a
    richer hover tooltip (status, latency, cost, block reason). Found and fixed a real z-index bug in
    the same pass: drei's `Html` defaults to a near-max z-index, so node labels floated above the fixed
    legend overlay regardless of DOM order — fixed by capping `zIndexRange={[1, 0]}` on both.
  - No icon appeared anywhere in the product — nav, stat cards, and empty states were plain text,
    which read as unfinished more than anything else about the visual design. Added a ~14-icon hand
    -written inline SVG set (`components/icons.tsx`) rather than a library — the product needs a small,
    fixed icon vocabulary at one size, which a dozen inline SVGs cover without a font/sprite dependency.
    Also added a shared `EmptyState` component (icon + heading + one line) replacing bare "No X yet"
    text on Approvals/Agents/Policies/Account.
- **Personal API tokens + Account page (post-launch, user-requested — "add an access token... system
  for outside users to use the product via APIs," "add account page to handle profiles").** Full
  detail in AUTH.md §4/decisions above; the short version: a third auth credential (`bstn_pat_...`)
  alongside agent keys and JWT sessions, routed through the same `authenticate_user`/RBAC path, scoped
  to the user who created it rather than shared org-wide. The user's mention of "access token and
  refresh token system" already existed for browser login (Phase 5) — the actual gap was that nothing
  let a *script* authenticate without an interactive login/refresh cycle, which personal tokens close
  without touching the existing JWT flow at all.
- **Render deployment: JWT keys via env var, frontend proxies to public URLs not private networking
  (post-launch, user-requested — "Ok deploy").** Live at
  [bastion-frontend.onrender.com](https://bastion-frontend.onrender.com): three Docker web services
  (interceptor, aggregator, frontend/nginx) plus a Key Value (Redis) instance, Neon for Postgres. Two
  real deployment-specific problems, found only by actually deploying and testing against the live
  URLs, not by reasoning about the infra in the abstract:
  - **JWT keys have no shared filesystem across Render services.** docker-compose shares the Ed25519
    keypair via a named volume populated by a one-shot key-generation container; Render has no
    equivalent since each service is an independent container. Fixed by having each service's
    entrypoint script write `JWT_PRIVATE_KEY_PEM`/`JWT_PUBLIC_KEY_PEM` env var content out to the file
    paths `config.py` already reads, if set — `config.py` itself untouched, so docker-compose/k8s
    (which never set these) behave exactly as before.
  - **Render's private per-service networking (`bastion-interceptor:4001`) never resolved**, despite
    matching every documented requirement — same region, same workspace, and (after fully deleting and
    recreating all four resources inside a dedicated project/environment to test that specific theory)
    same environment too. `nginx: [emerg] host not found in upstream` at boot, then, after switching to
    a resolver-based lookup deferred to request time, `could not be resolved (3: Host not found)` at
    request time instead — the DNS name genuinely isn't reachable through whatever this container's
    resolver is, and no further-documented fix was found. Rather than keep chasing an apparently
    undocumented private-DNS requirement, the frontend's nginx now proxies to the same public HTTPS
    URLs a browser would use directly — proven to work (a real signup ran against the deployed
    interceptor before this decision was even made), and no different in trust level since the public
    endpoint already *is* the production access point. Found two more real bugs applying this fix
    before it actually worked: an nginx directive-ordering bug (`rewrite ... break` silently prevents
    any `set` directive placed after it in the same block from ever running, leaving `proxy_pass` with
    an empty variable) and a `Host` header bug (forwarding the frontend's own hostname instead of
    `$proxy_host` got a 403 from Cloudflare, since Render's edge routes and authorizes by Host header).
  - Verified live end-to-end, not just per-service health checks: a real signup through
    `bastion-frontend.onrender.com`'s actual UI (not curl) — org creation, JWT issuance with the
    production keypair, redirect into a working dashboard — confirmed in a real browser against the
    deployed stack.
- **Overview became the new "/" landing page, Graph moved to "/graph".** The product previously opened
  straight into the 3D live graph, which is compelling once you have an agent running but is a dead end
  for literally everyone's first session (zero agents, zero traces, nothing to look at). An at-a-glance
  home page — agent/policy/approval counts, recent traces, an explicit "create an agent → write a
  policy → watch it live" checklist when the org is empty — replaces that with something that actually
  orients a new user, composed entirely from existing list endpoints rather than new backend
  aggregation surface.
