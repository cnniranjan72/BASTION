// Load test for POST /intercept (BUILD_PLAN.md Phase 9) — the latency-
// critical hot path (ARCHITECTURE.md §2.2, target <50ms p99 overhead).
// Run via Docker so no local k6 install is needed:
//
//   docker run --rm -i --network host \
//     -e INTERCEPTOR_URL=http://localhost:4001 \
//     -e AGENT_ID=44444444-4444-4444-4444-444444444444 \
//     -e AGENT_API_KEY=prompt-injection-demo-key \
//     grafana/k6 run - < infra/load-test/intercept.js
//
// Each VU iteration uses a fresh trace_id (a new root span), not a shared
// one — same-trace inserts serialize behind a per-trace advisory lock
// (docs/ARCHITECTURE.md §9/§12), which is correct and necessary for
// ordering, but would make this measure lock contention instead of the
// thing actually under test: per-request decision + event-write overhead.
import http from 'k6/http';
import { check } from 'k6';
import { uuidv4 } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

const INTERCEPTOR_URL = __ENV.INTERCEPTOR_URL || 'http://localhost:4001';
const AGENT_ID = __ENV.AGENT_ID || '44444444-4444-4444-4444-444444444444';
const AGENT_API_KEY = __ENV.AGENT_API_KEY || 'prompt-injection-demo-key';

const RATE = Number(__ENV.K6_RATE || 50);

export const options = {
  scenarios: {
    steady_load: {
      executor: 'constant-arrival-rate',
      rate: RATE, // requests/sec
      timeUnit: '1s',
      duration: '30s',
      preAllocatedVUs: 20,
      maxVUs: 200,
    },
  },
  thresholds: {
    // Documents the target from ARCHITECTURE.md §6, doesn't gate the run —
    // see docs/ARCHITECTURE.md §18 for why the honest number (synchronous
    // event writes) may not clear it, and why that's a deliberate tradeoff.
    http_req_duration: ['p(99)<50'],
  },
};

export default function () {
  const body = JSON.stringify({
    trace_id: uuidv4(),
    parent_span_id: null,
    tool_name: 'tickets.read',
    args: { ticket_id: 'LOAD-TEST' },
    agent_id: AGENT_ID,
  });

  const res = http.post(`${INTERCEPTOR_URL}/intercept`, body, {
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${AGENT_API_KEY}`,
    },
  });

  check(res, {
    'status is 200': (r) => r.status === 200,
    'decision is allowed': (r) => {
      try {
        return JSON.parse(r.body).decision === 'allowed';
      } catch {
        return false;
      }
    },
  });
}
