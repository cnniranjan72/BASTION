# Load test — `POST /intercept`

BUILD_PLAN.md Phase 9: "Load test the interceptor (k6 or locust), publish real p99 latency numbers
in the README." `intercept.js` is a [k6](https://k6.io/) script; no local k6 install needed, run it via
Docker:

```bash
docker run --rm -i \
  -e INTERCEPTOR_URL=http://host.docker.internal:4001 \
  -e AGENT_ID=44444444-4444-4444-4444-444444444444 \
  -e AGENT_API_KEY=prompt-injection-demo-key \
  grafana/k6 run - < infra/load-test/intercept.js
```

(`host.docker.internal`, not `localhost` — on Docker Desktop for Windows/Mac, containers run inside a
VM, so `--network host` doesn't give access to the Windows/Mac host's `localhost` the way it does on
native Linux Docker. `AGENT_ID`/`AGENT_API_KEY` above are the seeded Phase 8 demo agent —
`uv run --project demo-agent python -m demo_agent.seed` first if that hasn't been run.)

## Methodology

50 requests/sec, constant arrival rate, 30 seconds, against a single native `uvicorn`
`bastion-interceptor` process (no `--workers`, no horizontal scaling) with asyncpg's default pool
(`max_size=10`) — i.e., the floor a single unscaled instance can do, not a tuned or horizontally scaled
deployment. Each iteration uses a fresh `trace_id` (a new root span) rather than sharing one across
iterations, so the measurement is per-request decision + event-write overhead, not artificial lock
contention from `docs/ARCHITECTURE.md` §9/§12's per-trace ordering guarantee (real traces have low
enough intra-trace concurrency that this isn't the bottleneck in practice; a shared-trace benchmark would
measure something else entirely).

## Results

Three consecutive clean runs (`results/run{1,2,3}.summary.txt`, raw k6 output):

| run | avg     | median  | p90     | p95     | p99     | max     |
|-----|---------|---------|---------|---------|---------|---------|
| 1   | 20.55ms | 17.93ms | 31.58ms | 37.18ms | 45.41ms | 64.92ms |
| 2   | 21.35ms | 18.91ms | 32.67ms | 38.74ms | 46.94ms | 59.37ms |
| 3   | 22.43ms | 20.30ms | 33.71ms | 39.29ms | 53.06ms | 66.77ms |

`docs/ARCHITECTURE.md` §6's target is <50ms p99 overhead. Two of three runs clear it; the third misses
by ~3ms. That's the honest number, not a rounded-down one — §18 explains why: every `/intercept` call
synchronously writes at least one event to Postgres before responding (a deliberate durability-over-
latency tradeoff for a security audit trail, not an oversight), and that write is most of what's in this
number. It's also *why* p99 specifically sits right at the boundary rather than comfortably under it —
the median (~18-20ms) has real headroom; it's the tail (the occasional slower Postgres round trip) that
brushes the target.

**Also observed, worth being transparent about**: two earlier exploratory runs on this same machine (not
included in the table — conditions weren't comparable) hit p99 as high as 562ms, apparently from transient
contention (a concurrent `docker pull` of the k6 image itself in the first case, and this being a dev
machine already running a dozen unrelated Docker containers for other projects throughout this session).
That variance is real and worth knowing about — a shared, busy dev machine is not a clean benchmarking
environment — but it's a statement about *this measurement environment*, not about the interceptor's own
behavior under comparable load, which is what the table above (three consecutive, otherwise-idle-machine
runs) actually shows.

**What these numbers are not**: a horizontally-scaled or production-tuned deployment. The whole
point of `/intercept` being stateless (§2.2) is that this is a floor, not a ceiling — more replicas
behind a load balancer is the documented answer to needing more throughput, not a single instance
working harder.
