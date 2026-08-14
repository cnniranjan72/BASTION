# BASTION on Kubernetes

BUILD_PLAN.md Phase 9: "K8s manifests (even if only run via `kind`/`minikube` for the demo) with
documented scaling reasoning." That's the scope here — a real, runnable local cluster deploy, not a
cloud-specific production setup.

## What's here, and what's deliberately not

- `00-namespace.yaml`, `01-config.yaml`, `02-secrets.example.yaml` (template, not real secrets),
  `03-interceptor.yaml`, `04-aggregator.yaml`, `05-frontend.yaml` — Deployments + Services (+ one HPA)
  for the three BASTION services.
- **No Postgres/Redis manifests.** Running stateful databases well on Kubernetes (StatefulSets,
  PersistentVolumes, backup/restore, failover) is its own large problem that a demo deploy shouldn't
  pretend to solve. `docs/ARCHITECTURE.md` §7 already made this call for the deployed environment: a
  managed Postgres (Neon) rather than self-hosting one. The same reasoning extends here — point
  `bastion-db-redis`'s `DATABASE_URL`/`REDIS_URL` at managed services, don't run Postgres/Redis
  in-cluster for anything beyond a `kind`/`minikube` smoke test using `infra/docker/docker-compose.yml`'s
  images ported over manually, which isn't set up here since it isn't what a real deploy would do.

## Running on `kind`

```bash
kind create cluster --name bastion

# Build the three images (from repo root — see each Dockerfile's own comment
# on why the build context has to be the repo root, not the service directory).
docker build -f interceptor/Dockerfile -t bastion-interceptor:latest .
docker build -f aggregator/Dockerfile -t bastion-aggregator:latest .
docker build -f frontend/Dockerfile -t bastion-frontend:latest .

kind load docker-image bastion-interceptor:latest --name bastion
kind load docker-image bastion-aggregator:latest --name bastion
kind load docker-image bastion-frontend:latest --name bastion

kubectl apply -f infra/k8s/00-namespace.yaml
kubectl apply -f infra/k8s/01-config.yaml
# Edit 02-secrets.example.yaml with real values first, or use the
# `kubectl create secret` commands in its own header comment instead of
# applying the file directly.
kubectl apply -f infra/k8s/02-secrets.example.yaml
kubectl apply -f infra/k8s/03-interceptor.yaml
kubectl apply -f infra/k8s/04-aggregator.yaml
kubectl apply -f infra/k8s/05-frontend.yaml

kubectl -n bastion get pods -w
```

Migrations aren't a manifest here (no Job) — for a `kind` smoke test, port-forward Postgres and run
`infra/db/migrate.py` from the host the same way local dev does, pointed at whatever `DATABASE_URL`
the secret uses. A real deploy would run this as a pre-deploy step in CI, not a cluster-internal Job.

```bash
kubectl -n bastion port-forward svc/bastion-frontend 8080:8080
# open http://localhost:8080
```

## Scaling reasoning, summarized (full detail in each manifest's own comments)

- **Interceptor**: stateless by design (`docs/ARCHITECTURE.md` §2.2) — the in-memory `PolicyCache` is a
  rebuildable cache (bootstrapped on startup, kept current via Redis pub/sub), never a source of truth.
  Scaling is just more replicas; the included HPA scales on CPU as a simple first cut, with a comment on
  why the real signal would be `intercept_latency_seconds` p99 via a Prometheus Adapter instead (I/O-bound
  workload, weak correlation to CPU).
- **Aggregator**: each replica independently receives every Postgres `NOTIFY` (Postgres broadcasts to all
  listeners) and only pushes to the WebSocket clients connected to *that* replica — no shared fan-out
  layer needed between replicas for correctness. Detailed in `04-aggregator.yaml`'s header comment.
- **Frontend**: static files + a reverse proxy, genuinely stateless, scales trivially.
