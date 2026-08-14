# Contributing

This started as a solo build following a fixed phase plan (`docs/BUILD_PLAN.md`), not a project actively
soliciting outside contributions — but the standards below are real, not performative, and apply to any
change, from anyone.

## Before changing anything

- Read `docs/ARCHITECTURE.md` before touching a service boundary, `docs/DATA_MODEL.md` before touching
  schema, `docs/API_SPEC.md` before touching a wire contract. If the docs and the code disagree, that's
  a bug in one of them — fix the mismatch, don't just work around it (see `docs/decisions.md` for
  examples of exactly this happening and getting caught).
- Check `docs/decisions.md` for whether the thing you're about to "fix" was actually a deliberate
  tradeoff with reasoning behind it (e.g. §18's synchronous event writes) rather than an oversight.

## Standing rules (from `docs/CLAUDE.md`, still in force)

1. **`events` is append-only.** Never write code that updates or deletes a row in it. Enforced by a DB
   trigger, not just convention — don't try to work around the trigger either.
2. **Every request gets a `request_id`**, logged at entry/exit, included in error responses.
3. **No mock data pretending to be a real integration.** If something's a stand-in (the demo agent's
   fake payments API, its deterministic tool-selection instead of a real LLM call), say so in a comment
   and in the relevant doc — don't let it look real by omission.
4. **`/intercept` is latency-critical.** Know the tradeoff in `docs/decisions.md` §18 before changing
   anything on that path, and re-run `infra/load-test/` before and after if you do.
5. **Tests alongside the change, not after.** A change isn't done until the workspace suite passes.
6. **Auth is not decorative.** Refresh token reuse detection, RBAC, org scoping — these are correctness
   requirements, not nice-to-haves, and every one of them already has a test proving the failure mode it
   exists to prevent. Add one for anything new in this area.
7. **Multi-tenancy from day one.** Any new query touching `agents`/`policies`/`traces`/`events` needs
   `org_id` scoping and a test proving org A can't read org B's data.

## Before opening a PR

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy shared/src interceptor/src aggregator/src sdk-python/bastion demo-agent/demo_agent
uv run pytest shared/tests interceptor/tests aggregator/tests sdk-python/tests demo-agent/tests
cd frontend && npm run typecheck && npm run lint
```

All of the above run in CI (`.github/workflows/ci.yml`) — matching them locally first saves a round
trip, not a formality.

## Commit messages

Explain *why*, not just *what* — the diff already shows what changed. If the change deviates from a
spec doc or fixes something a doc got wrong, say so and update the doc in the same commit; don't leave
the two disagreeing for someone else to notice later (this project's own git history has several
examples of doing this right — e.g. the Phase 9 commit documenting the Redis port collision it found
and fixed in the same change).

## Reporting a bug

Include the request/response or the failing test, not just a description. If it's a security-relevant
bug (something a malicious agent could exploit to bypass interception, read another org's data, or
forge/replay a token) and this repo has a maintainer contact listed elsewhere by the time you're reading
this, use that instead of a public issue — no disclosure process is set up here yet.
