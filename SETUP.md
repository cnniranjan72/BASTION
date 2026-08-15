# SETUP

Three ways to run BASTION, in order of how much you're trying to verify:

1. **Native dev** — services run directly (`uv run uvicorn`, `npm run dev`), Postgres/Redis in Docker.
   Fastest iteration loop; what every phase of this project was actually built and tested against.
2. **Full Docker Compose stack** — every service containerized, one command. Closest to "clone and run."
3. **Kubernetes (`kind`)** — a real cluster, real Deployments/Services, port-forwarded. What Phase 9's
   K8s manifests actually deploy, verified against a real `kind` cluster, not just written and hoped.

All three were run for real during development (`docs/PROGRESS.md`'s Phase 9 entry) — none of this is
aspirational.

## Prerequisites

- Docker Desktop (or equivalent) running
- [`uv`](https://docs.astral.sh/uv/) for the Python workspace
- Node.js 22+ and npm, for the frontend
- Nothing else required for options 1-2. Option 3 also needs `kubectl` and
  [`kind`](https://kind.sigs.k8s.io/) (a single binary, no admin rights needed to install).

## Option 1: native dev

```bash
# Postgres (host :5442) + Redis (host :6389) — see docs/ARCHITECTURE.md §7/§19
# for why neither is on its default port.
docker compose -f infra/docker/docker-compose.yml up -d postgres redis

uv sync --all-packages --all-extras --dev
uv run python infra/keys/generate_dev_keys.py
uv run python infra/db/migrate.py
uv run python infra/db/seed_dev.py              # base org + a login: demo@bastion.dev / demo-password-123
uv run --project demo-agent python -m demo_agent.seed   # Phase 8 demo agent + policy

# Each in its own terminal:
uv run --project interceptor uvicorn bastion_interceptor.main:app --port 4001
uv run --project aggregator uvicorn bastion_aggregator.main:app --port 4002
cd frontend && npm install && npm run dev        # http://localhost:5173
```

Verify everything's wired up:

```bash
curl http://localhost:4001/healthz
curl http://localhost:4002/healthz
uv run --project demo-agent python -m demo_agent.run_demo    # runs the prompt-injection scenario once
```

Log into `http://localhost:5173` with `demo@bastion.dev` / `demo-password-123`, connect the live view
to agent `44444444-4444-4444-4444-444444444444`, then run `run_demo.py` again and watch the blocked
call turn red in the graph in real time.

**Optional — BYOK / live LLM demo (U17, `docs/adr/ADR-022`)**: to use the "LLM provider keys" and
"Run the live prompt-injection demo" sections on `/account`, the interceptor needs
`BYOK_MASTER_KEY` set (generate one with
`uv run python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"`) — without
it, that feature fails closed rather than silently storing keys unsafely. For local Ollama (no key
needed): install Ollama, `ollama pull llama3.1`, and it's used automatically when you pick
"ollama (local)" in the demo's provider dropdown (`OLLAMA_BASE_URL` defaults to
`http://localhost:11434`). See `frontend/src/components/DocsPage.tsx` (`/docs` once logged in) for
the full walkthrough.

## Option 2: full Docker Compose stack

```bash
docker compose -f infra/docker/docker-compose.yml up -d --build
```

This builds and runs every service — `postgres`, `redis`, one-shot `migrate`/`generate-keys` init jobs,
`interceptor`, `aggregator`, `frontend` (nginx, reverse-proxying to the other two — see
`frontend/nginx.conf`) — from a single command. Seed data the same way as Option 1 (`seed_dev.py`,
`demo_agent.seed`) — those connect to the same Postgres/Redis either way, from the host, at the same
ports. Open `http://localhost:8080`.

```bash
docker compose -f infra/docker/docker-compose.yml down       # stop everything, keep data
docker compose -f infra/docker/docker-compose.yml down -v    # stop everything, wipe volumes too
```

## Option 3: Kubernetes (`kind`)

Full instructions and scaling-reasoning writeup: `infra/k8s/README.md`. Short version:

```bash
kind create cluster --name bastion
docker build -f interceptor/Dockerfile -t bastion-interceptor:latest .
docker build -f aggregator/Dockerfile -t bastion-aggregator:latest .
docker build -f frontend/Dockerfile -t bastion-frontend:latest .
kind load docker-image bastion-interceptor:latest bastion-aggregator:latest bastion-frontend:latest --name bastion

kubectl apply -f infra/k8s/00-namespace.yaml -f infra/k8s/01-config.yaml
# Real secret values, not the committed template — see 02-secrets.example.yaml's own header.
kubectl -n bastion create secret generic bastion-db-redis --from-literal=DATABASE_URL=... --from-literal=REDIS_URL=...
kubectl -n bastion create secret generic bastion-jwt-keys --from-file=jwt_private.pem=infra/keys/jwt_private.pem --from-file=jwt_public.pem=infra/keys/jwt_public.pem
kubectl apply -f infra/k8s/03-interceptor.yaml -f infra/k8s/04-aggregator.yaml -f infra/k8s/05-frontend.yaml

kubectl -n bastion get pods -w
kubectl -n bastion port-forward svc/bastion-frontend 8080:8080
```

## Running the test suite

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy shared/src interceptor/src aggregator/src sdk-python/bastion demo-agent/demo_agent
uv run pytest shared/tests interceptor/tests aggregator/tests sdk-python/tests demo-agent/tests

cd frontend && npm run typecheck && npm run lint
```

## Load testing

`infra/load-test/README.md` — a k6 script against `POST /intercept`, runnable via Docker with no local
k6 install. Real numbers from three runs are in that file and in the main README.

## Troubleshooting

- **Postgres/Redis connection refused, but the containers say healthy**: this machine's Postgres/Redis
  aren't on their default ports (5442 / 6389) specifically because of collisions found during
  development (`docs/ARCHITECTURE.md` §7, §19) — double check `DATABASE_URL`/`REDIS_URL` if you've
  overridden them, and that nothing else on your machine (a native install, another project's compose
  file) already owns those exact ports either.
- **"attached to a different loop" in pytest**: each workspace package's own `pyproject.toml` needs
  `asyncio_default_fixture_loop_scope = "session"` — pytest uses the nearest ini file it finds, not a
  merge of every one up the tree, so this has to be set per-package, not just at the repo root
  (`docs/PROGRESS.md`'s Phase 1 log has the full story; this bit us again in Phase 8 when a new package
  forgot it).
- **`ImagePullBackOff` on `kind`**: `kind load docker-image` puts the image on the cluster's nodes, but
  a `:latest` tag defaults to `imagePullPolicy: Always`, which ignores that and tries a registry pull
  anyway. Every manifest in `infra/k8s/` already sets `imagePullPolicy: IfNotPresent` for this reason.
