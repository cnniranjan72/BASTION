# BASTION — Progress Log

## Status: Phase 0 complete
Phase: 0 (scaffolding) → next up: Phase 1 (event core + interceptor)

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
    (`structlog`), and a `request_id` middleware (bind at entry, log at entry+exit, echoed as
    `X-Request-Id` header) per CLAUDE.md rule #2. No `/intercept` logic yet — that's Phase 1.
  - Python monorepo tooling: `uv` workspace (root `pyproject.toml`, members `shared`/`interceptor`/
    `aggregator`/`sdk-python`), `ruff` (lint+format) and `mypy --strict` clean across all packages,
    `pytest` (9 tests passing: schema validation, both services' health checks, SDK import smoke test).
  - Docker Compose (`infra/docker/docker-compose.yml`): Postgres 16 + Redis 7, both healthy.
    **Postgres mapped to host port 5442, not 5432** — this machine already runs other local Postgres
    containers on 5432; documented in ARCHITECTURE.md §7 and reflected in `.env.example` +
    both services' `config.py` defaults. CI's Postgres service container is unaffected (runs on 5432
    inside an isolated GitHub Actions runner).
  - CI (`.github/workflows/ci.yml`): `uv sync` → `ruff check` → `ruff format --check` → `mypy` →
    `pytest`, against live Postgres+Redis service containers. Not yet run on GitHub (no remote pushed
    yet, local-only repo so far).
  - Verified end-to-end by hand: both services boot via `uvicorn`, hit `/healthz` against the real
    Docker Postgres/Redis stack, `X-Request-Id` header present on responses.
  - No "Warden" references found anywhere in `docs/` — nothing to rename yet; will keep checking as
    new docs/code are touched.

## Next up
- Phase 1: `events` table + append-only trigger (reject UPDATE/DELETE), `POST /intercept` with a
  hardcoded policy (no DSL yet), event emission (`CallAttempted`/`CallAllowed`/`CallBlocked`), the
  real Python SDK `BASTION.call()` wrapper, and the milestone test proving concurrent nested-call
  causal ordering reconstructs correctly.

## Known deviations from BUILD_PLAN.md
- None in phase *order* — Phase 0 is still Phase 0. The interceptor/aggregator implementation
  language wasn't specified by BUILD_PLAN.md/ARCHITECTURE.md; the FastAPI decision (and the
  Node/TS false start that preceded it) is recorded above and in `docs/ARCHITECTURE.md` §7, not a
  plan-order deviation.

## Open questions / decisions needed
(none currently blocking — will flag here if Phase 1's append-only trigger design or causal-ordering
test raises anything ambiguous in DATA_MODEL.md)
