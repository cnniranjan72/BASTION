# CLAUDE_UPGRADE.md — Addendum to CLAUDE.md for the v2 upgrade

Read this alongside the existing `CLAUDE.md` — it doesn't replace it, it adds rules specific to the v2 upgrade work.

## New required reading before touching v2 work
- `UPGRADE_ARCHITECTURE.md` — the target architecture and every correctness guarantee
- `FRONTEND_V2.md` — the target frontend, flagship-experience-first
- `UPGRADE_BUILD_PLAN.md` — phase order (U1–U16), follow it, don't skip to U15 (frontend) early
- `adr/ADR_TEMPLATE.md` and `adr/ADR_INDEX.md` — every required decision record

## Standing rules specific to v2
1. **A phase is not done without its milestone test passing AND its ADR(s) written.** Not one or the other.
2. **Write the ADR at the point the decision is made**, not retroactively from memory at the end — context is best captured while it's live.
3. **State machine discipline (U1):** all call-status transitions go through the single state machine module. If you find a code path bypassing it while working on a later phase, stop and fix it before continuing — don't layer more correctness work on top of a leak.
4. **Idempotency is not optional for side-effecting tool calls.** If a new tool type is added later without an idempotency key path, that's a bug, not a shortcut.
5. **Kafka is distribution, Postgres is truth.** Any code that treats Kafka as authoritative (e.g. rebuilding state only from Kafka with no reconciliation against Postgres) is wrong per UPGRADE_ARCHITECTURE.md §4.2 — flag it if you see it, including in code you wrote earlier in the session.
6. **Explicit non-guarantees stay explicit.** Don't accidentally imply global event ordering anywhere in code, comments, or docs — only same-partition/same-trace ordering is guaranteed.
7. **Chaos and load test results are real artifacts, not aspirational text.** If a chaos scenario fails on first attempt, that's expected and good — record what failed and how it was fixed in `docs/CHAOS_RESULTS.md`, don't quietly retry until it passes and hide the first failure.
8. **Frontend v2 rule (extends CLAUDE.md's no-mock-data rule):** no screen in FRONTEND_V2.md gets built against mocked backend state. If its real dependency isn't built yet, build the dependency first or explicitly defer the screen in PROGRESS.md.

## Update PROGRESS.md format for v2
Add a `## v2 Upgrade` section to the existing PROGRESS.md (don't create a second progress file) with the same fields as before (phase, done, broken, next) plus:
- Which ADRs are written vs still open (mirror `ADR_INDEX.md` checkboxes)
- Chaos/load test results status (not started / in progress / complete, with a link to `CHAOS_RESULTS.md` once it exists)

## Definition of done for the full v2 upgrade
1. All of U1–U16 complete per UPGRADE_BUILD_PLAN.md.
2. All ADRs in `ADR_INDEX.md` checked off.
3. `docs/CHAOS_RESULTS.md` exists with real results.
4. README.md updated with real load-test numbers and the SLO table with pass/fail status against measured reality.
5. FRONTEND_V2.md's three flagship experiences are live and wired to real backend state, verified per U15's milestone test.
