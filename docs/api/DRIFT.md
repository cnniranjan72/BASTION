# API docs: generated vs. hand-written, and what drifted

`docs/API_SPEC.md` is the hand-written contract, written before (and updated alongside) the code
throughout every phase. `interceptor.openapi.json` / `aggregator.openapi.json` in this directory are
**generated**, straight from the running services' own FastAPI schema — the actual source of truth for
what the code does, not what a doc says it does. Regenerate them any time with the services running
locally:

```bash
curl -s http://localhost:4001/openapi.json | python -m json.tool > docs/api/interceptor.openapi.json
curl -s http://localhost:4002/openapi.json | python -m json.tool > docs/api/aggregator.openapi.json
```

Comparing the two surfaced three real things, all fixed in `API_SPEC.md` directly (not just noted
here) except the one that's a genuine, permanent limitation of OpenAPI itself:

1. **Wrong base URL.** `API_SPEC.md` originally documented `Base URL: /api/v1` and a
   `"poll_url": "/api/v1/approvals/{id}"` example. Neither service has ever had a `/api/v1` prefix — no
   `APIRouter(prefix=...)`, nothing — every endpoint is served at the bare path (`grep` for `api/v1`
   across both services' source: zero matches). This was a spec written before the code and never
   reconciled once the code diverged; nothing consumes the doc's version-prefixed paths (the frontend's
   dev proxy and every test in the suite already use the bare paths). Fixed: the base URL line now
   states the actual routing, with this history noted rather than silently deleted.
2. **Missing `reason` field in the WS `node_updated` example.** Added to the real message
   (`shared/src/bastion_shared/realtime.py`) during Phase 8's live-verification bugfix
   (`docs/ARCHITECTURE.md` §17) but the doc's example JSON was never updated alongside it. Fixed.
3. **WebSocket routes don't appear in the generated OpenAPI schema at all.** This isn't a bug in either
   the code or the doc — OpenAPI 3.x (what FastAPI generates) has no representation for WebSocket
   endpoints, so `WS /live/{agent_id}` is structurally invisible to `openapi.json` no matter how correct
   the implementation is. `API_SPEC.md`'s hand-written "Realtime API" section is the only place this
   contract is documented, and has to stay that way — an automated drift check between the generated
   schema and the spec can only ever cover the HTTP surface, not the WebSocket one. Worth knowing before
   trusting "the generated docs are the source of truth" as a blanket rule.

Also confirmed, not drift: `GET /metrics` on both services (Prometheus text exposition, Phase 9) is
absent from the generated OpenAPI schema by design (`include_in_schema=False`) — it's not a REST
resource with a JSON contract, documenting it as one would be actively misleading. Noted in
`API_SPEC.md` directly instead.

## What this check does *not* cover

This was a one-time manual diff for Phase 11, not a CI gate — there's no automated test failing a build
if `API_SPEC.md` and the real schema diverge again next time a field changes. A real next step (noted,
not built, per CLAUDE.md rule #3's "say so explicitly" rather than silently leaving a TODO) would be a
CI job that regenerates both `openapi.json` files and diffs them against committed copies, failing if
they've changed without `docs/API_SPEC.md` being touched in the same PR.
