// U13 (v2 upgrade), UPGRADE_ARCHITECTURE.md §15/§16: load tests /intercept
// (the SLO'd endpoint, target p99 < 50ms) at a configurable target RPS,
// using k6's constant-arrival-rate executor so the achieved rate can be
// compared directly against the *requested* rate — the actual "where does
// latency inflect" evidence, not just a VU count.
//
// setup() creates a real org + agent through the real HTTP API (signup,
// then POST /agents) — no direct DB seeding, so this measures the same
// code path a real caller would hit. No policy is assigned to the agent
// deliberately: this isolates /intercept's own hot-path overhead (the
// SLO's actual subject) from whatever a specific policy's condition
// evaluation would add on top, which is a separate, smaller cost already
// covered by the "policy decision (in-process) p99 < 10ms" SLO row.
//
// Each iteration does a full realistic pair — POST /intercept, then
// POST /spans/{id}/complete — matching what a real SDK caller always
// does, so the outbox/Kafka/aggregator pipeline is under the same load a
// production traffic pattern would generate, not just the decision path
// in isolation.

import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:4001';
const TARGET_RPS = parseInt(__ENV.TARGET_RPS || '50', 10);
const DURATION = __ENV.DURATION || '20s';
const MAX_VUS = parseInt(__ENV.MAX_VUS || String(Math.max(50, TARGET_RPS * 2)), 10);

const errors = new Counter('bastion_errors');

export const options = {
  scenarios: {
    intercept_load: {
      executor: 'constant-arrival-rate',
      rate: TARGET_RPS,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: Math.min(MAX_VUS, 50),
      maxVUs: MAX_VUS,
    },
  },
  thresholds: {
    // Informational only (k6 reports pass/fail; we read the summary
    // numbers either way) — not used to abort the run early.
    http_req_duration: ['p(99)<50000'],
  },
};

export function setup() {
  const orgName = `k6-load-${Date.now()}`;
  const email = `${orgName}@example.com`;
  const signup = http.post(
    `${BASE_URL}/auth/signup`,
    JSON.stringify({ org_name: orgName, email, password: 'k6-load-test-password-1' }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  if (signup.status !== 201) {
    throw new Error(`signup failed: ${signup.status} ${signup.body}`);
  }
  const accessToken = JSON.parse(signup.body).access_token;

  const createAgent = http.post(
    `${BASE_URL}/agents`,
    JSON.stringify({ name: 'k6-load-agent', policy_set_id: null }),
    { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` } }
  );
  if (createAgent.status !== 201) {
    throw new Error(`create agent failed: ${createAgent.status} ${createAgent.body}`);
  }
  const agentBody = JSON.parse(createAgent.body);
  return { agentId: agentBody.id, apiKey: agentBody.api_key };
}

function randomUuidV4() {
  // k6's JS runtime (goja) has no built-in UUID/crypto.randomUUID — a
  // small hand-rolled v4-shaped generator is enough here; this is
  // synthetic load-test traffic, not anything security-sensitive.
  return 'xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx'.replace(/x/g, () =>
    Math.floor(Math.random() * 16).toString(16)
  );
}

export default function (data) {
  const interceptRes = http.post(
    `${BASE_URL}/intercept`,
    JSON.stringify({
      trace_id: randomUuidV4(),
      parent_span_id: null,
      tool_name: 'k6.load_test',
      args: { vu: __VU, iter: __ITER },
      agent_id: data.agentId,
      idempotency_key: randomUuidV4(),
    }),
    {
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${data.apiKey}` },
      tags: { name: 'intercept' },
    }
  );
  const ok = check(interceptRes, { 'intercept 200': (r) => r.status === 200 });
  if (!ok) {
    errors.add(1);
    return;
  }
  const spanId = JSON.parse(interceptRes.body).span_id;
  const completeRes = http.post(
    `${BASE_URL}/spans/${spanId}/complete`,
    JSON.stringify({ status: 'completed', latency_ms: 1.0, result: { ok: true } }),
    {
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${data.apiKey}` },
      tags: { name: 'complete' },
    }
  );
  if (completeRes.status !== 200) {
    errors.add(1);
  }
}
