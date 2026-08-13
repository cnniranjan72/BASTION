# CLAUDE.md — Working rules for Claude Code on the BASTION project

This file is read by Claude Code at the start of every session in this repo. Follow it strictly.

## Project context
BASTION is an AI agent control plane (interception + policy engine + event-sourced replay + live 3D visualization). Full specs live in this directory:
- `PRD.md` — what we're building and why
- `ARCHITECTURE.md` — system design, read before touching any service boundary
- `DATA_MODEL.md` — schema, source of truth for all tables
- `AUTH.md` — auth implementation rules, follow exactly (refresh rotation + reuse detection is not optional)
- `API_SPEC.md` — contract for every endpoint, keep in sync with actual code
- `BUILD_PLAN.md` — the phase order. **Do not skip ahead to Phase 7 (3D UI) before Phases 1–6 have passing tests.**

Read the relevant doc before starting work on any component. If a doc and the code disagree, stop and flag it — don't silently pick one.

## Standing engineering rules
1. **Event sourcing discipline**: the `events` table is append-only. Never write code that updates or deletes a row in `events`. Any "current state" is a fold over events or a clearly-labeled projection/cache that can be rebuilt.
2. **Every service call gets a `request_id`**, logged at entry and exit, included in error responses.
3. **No mock data pretending to be real integrations** in anything past Phase 4. If a downstream API isn't built yet, say so explicitly in code comments and README — don't fake a response silently.
4. **Latency-critical path (`/intercept`) never blocks on non-essential work.** Event writes to the log can be fire-and-forget/async; the policy decision itself must not wait on anything but the in-memory policy cache.
5. **Write tests alongside each phase**, not after. A phase isn't "done" until its milestone test (defined in BUILD_PLAN.md) passes.
6. **Auth is not decorative.** Implement AUTH.md exactly, including refresh token reuse detection. This is a graded requirement, not a nice-to-have.
7. **Multi-tenancy from day one.** Every query touching `agents`, `policies`, `traces`, `events` must be scoped by `org_id`. Write one test that proves org A cannot read org B's data.

## Skills and tools to use
- Use the `frontend-design` skill before writing any React/Three.js UI code — the 3D view needs deliberate visual design, not default Tailwind/Three.js scaffolding.
- Use the `xlsx`/`docx`/`pptx`/`pdf` skills only if asked to produce those file types (e.g. a pitch deck from these docs) — not for building the app itself.
- If a task would benefit from a project-specific skill (e.g. a reusable "write a BASTION policy YAML" helper, or a "generate a load-test scenario" skill), propose creating one rather than repeating the same manual steps each session.

## Memory / continuity across sessions
Claude Code should maintain a running log so work is resumable across sessions:
- Keep `PROGRESS.md` in this directory updated after every work session: what phase is active, what's done, what's broken, what's next. Write to it before ending a session, not just when asked.
- When a non-obvious design decision is made that isn't already in one of the spec docs (e.g. "chose at-least-once delivery for events, here's why"), add it to the relevant doc (usually ARCHITECTURE.md) so future sessions don't relitigate it.
- If you deviate from BUILD_PLAN.md's order for a good reason, record the reason in PROGRESS.md.

## Definition of done for any phase
1. Code implements what ARCHITECTURE.md/DATA_MODEL.md/API_SPEC.md describe for that phase.
2. The milestone test from BUILD_PLAN.md passes.
3. PROGRESS.md is updated.
4. No TODOs left silently in latency-critical paths — either resolved or explicitly logged as a known limitation in the README.

## Tone/behavior for this project specifically
- Prioritize correctness and honest scope over impressiveness. A working Phase 1–6 with a plain table UI is a better artifact than a stunning 3D view sitting on top of broken event ordering.
- When something in the spec is ambiguous or you think it's wrong, say so directly and propose an alternative — don't silently implement a guess.
- Surface real numbers (latency, load test results) rather than descriptive claims wherever possible.
