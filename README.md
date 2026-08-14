# BASTION

AI agent control plane: interception, policy enforcement, event-sourced replay, live 3D execution graph.

**Status: Phase 0 (scaffolding) — see `docs/PROGRESS.md` for the current build state.**

This is a placeholder README. The full version (architecture diagram, quickstart, demo
instructions, real latency/scale numbers) is written once the build plan in
`docs/BUILD_PLAN.md` is complete — see `docs/PRD.md` and `docs/ARCHITECTURE.md` in the
meantime for what this is and how it's designed.

## Repo layout

- `interceptor/` — latency-critical hot-path service (`POST /intercept`), Python + FastAPI
- `aggregator/` — event-stream subscriber, graph builder, WebSocket fan-out, Python + FastAPI
- `frontend/` — React + react-three-fiber live execution graph (not yet scaffolded; Phase 7)
- `sdk-python/` — `BASTION.call()` client SDK
- `shared/` — `bastion_shared`, Pydantic models that are the single source of truth for the
  event/policy/API wire shape — imported directly by `interceptor`, `aggregator`, and
  `sdk-python` (see `docs/ARCHITECTURE.md` §7 for why, and how the frontend stays in sync)
- `infra/` — Docker Compose, Dockerfiles, Kubernetes manifests
- `docs/` — specs (PRD, architecture, data model, auth, API contract, build plan, progress log)

## Local dev (Phase 0)

The Python side (`shared`, `interceptor`, `aggregator`, `sdk-python`) is a single
[`uv`](https://docs.astral.sh/uv/) workspace — one lockfile, one venv, editable cross-package
installs.

```bash
docker compose -f infra/docker/docker-compose.yml up -d   # Postgres (host :5442) + Redis (host :6389)

uv sync --all-packages --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy shared/src interceptor/src aggregator/src sdk-python/bastion
uv run pytest shared/tests interceptor/tests aggregator/tests sdk-python/tests

# run a service locally
uv run --project interceptor uvicorn bastion_interceptor.main:app --port 4001
uv run --project aggregator uvicorn bastion_aggregator.main:app --port 4002
```

`frontend/` is not scaffolded yet — see `docs/BUILD_PLAN.md` Phase 7. Depth before demo polish.
