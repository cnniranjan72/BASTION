import { Link } from "react-router-dom";
import { TopBar } from "./TopBar";

const SECTIONS = [
  { id: "overview", label: "Overview" },
  { id: "quickstart", label: "Quickstart" },
  { id: "local-dev", label: "Local development" },
  { id: "byok", label: "Bring your own LLM key" },
  { id: "api", label: "API reference" },
  { id: "deployment", label: "Deployment" },
  { id: "architecture", label: "Architecture" },
];

export function DocsPage() {
  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page docs-page">
        <div className="page__header">
          <h1>Documentation</h1>
          <p className="page__subtitle">
            What BASTION is, how to run it, and how its API is shaped.
          </p>
        </div>

        <div className="docs-layout">
          <nav className="docs-toc" aria-label="Documentation sections">
            {SECTIONS.map((s) => (
              <a key={s.id} href={`#${s.id}`}>
                {s.label}
              </a>
            ))}
          </nav>

          <div className="docs-content">
            <section id="overview" className="docs-section">
              <h2>Overview</h2>
              <p>
                BASTION sits between an AI agent and the tools it's allowed to call. Every tool
                call an agent makes goes through <code>POST /intercept</code> first — a real
                policy engine decides <strong>allow</strong>, <strong>block</strong>, or{" "}
                <strong>require approval</strong> before the call happens, and every decision is
                written to an append-only event log. The 3D graph and Incident Replay views you
                see elsewhere in this app are both built directly on that same event log — nothing
                in the visualization is a separate, disconnected representation of what actually
                happened.
              </p>
              <p>
                This page covers running BASTION yourself, wiring an agent up to it, bringing your
                own LLM key to run the live demo, and the shape of the API. Full technical
                documentation (architecture decisions, data model, every endpoint's contract) lives
                in this repository's <code>docs/</code> directory alongside the code.
              </p>
            </section>

            <section id="quickstart" className="docs-section">
              <h2>Quickstart: wire up an agent</h2>
              <p>
                Agents call BASTION through the Python SDK, not directly — the SDK generates
                trace/span IDs, retries idempotently, and continues distributed traces
                automatically.
              </p>
              <pre>
                <code>{`from bastion import BastionClient, BastionBlockedError

client = BastionClient(
    base_url="https://your-interceptor-url",
    api_key="<agent api key, from Agents → create>",
    agent_id="<agent id>",
)

try:
    result = await client.call(
        "payments.transfer",
        {"to": "vendor-123", "amount": 25.0, "memo": "invoice"},
        lambda: my_real_payments_api.transfer("vendor-123", 25.0),
    )
except BastionBlockedError as exc:
    # policy blocked this call — exc.reason explains why
    ...`}</code>
              </pre>
              <p>
                Create an agent and its API key from the <Link to="/agents">Agents</Link> page,
                and its policy from <Link to="/policies">Policies</Link> or the{" "}
                <Link to="/policy-studio">Policy Simulator</Link> (which lets you test a
                hypothetical call against the real policy engine before writing the policy).
              </p>
            </section>

            <section id="local-dev" className="docs-section">
              <h2>Local development</h2>
              <p>Native dev, Postgres/Redis in Docker, everything else run directly:</p>
              <pre>
                <code>{`docker compose -f infra/docker/docker-compose.yml up -d postgres redis
uv run python infra/keys/generate_dev_keys.py
uv run python infra/db/migrate.py
uv run python infra/db/seed_dev.py

uv run --project interceptor uvicorn bastion_interceptor.main:app --port 4001
uv run --project aggregator uvicorn bastion_aggregator.main:app --port 4002
cd frontend && npm install && npm run dev   # http://localhost:5173`}</code>
              </pre>
              <p>
                A full Docker Compose stack and a Kubernetes (<code>kind</code>) option also exist
                — see <code>SETUP.md</code> in the repository for both.
              </p>
              <h3>Running the demo agent against a local LLM (Ollama)</h3>
              <p>
                The scheduled reliability demo (<code>demo_agent.run_demo</code>, no flags) uses a
                deterministic stand-in for an LLM's decision, deliberately — a live model call in a
                "run 20 times, must never flake" test would make the test flaky by the nature of
                what it's testing, not a bug. To watch a <em>real</em> model make the decision
                instead, install{" "}
                <span className="docs-inline-mono">ollama</span>, pull a tool-calling-capable
                model, and run:
              </p>
              <pre>
                <code>{`ollama pull llama3.1
uv run --project demo-agent python -m demo_agent.run_demo --llm ollama`}</code>
              </pre>
              <p>
                This is exactly the same mechanism the "Run the live prompt-injection demo" button
                on your <Link to="/account">Account</Link> page uses server-side, against whichever
                provider you choose there.
              </p>
            </section>

            <section id="byok" className="docs-section">
              <h2>Bring your own LLM key</h2>
              <p>
                From <Link to="/account">Account</Link>, add an OpenAI, Anthropic, or Gemini API
                key to run the live prompt-injection demo with a real model instead of the
                deterministic stand-in. A few things worth knowing before you do:
              </p>
              <ul>
                <li>
                  Your key is encrypted at rest (AES-256-GCM) the moment it's submitted. It is
                  never stored in plaintext, never logged, and never returned by any API response
                  — only the last 4 characters are, for you to recognize which key is which.
                </li>
                <li>
                  It's decrypted for exactly one purpose: calling the provider's API when you click
                  "Run live demo." It is not used for anything else, and not shared across
                  organizations or users — a key belongs to the person who added it.
                </li>
                <li>
                  If the provider rejects the key (revoked, typo'd) or rate-limits it, the demo
                  reports that specific failure — BASTION can't raise your own provider account's
                  limits, but it won't hide why a run failed either.
                </li>
                <li>
                  Prefer not to share a key at all? Choose <strong>ollama (local)</strong> in the
                  provider dropdown instead — no key needed, but it only works when you're running
                  BASTION's interceptor on the same machine as a local Ollama install (see{" "}
                  <a href="#local-dev">Local development</a> above), not against a hosted
                  deployment.
                </li>
                <li>
                  Revoke a key any time from the same page — it stops working immediately.
                </li>
              </ul>
            </section>

            <section id="api" className="docs-section">
              <h2>API reference</h2>
              <p>
                Two APIs: a machine API the SDK calls on every tool call (latency-critical), and a
                human/dashboard API this frontend calls. Every response uses one error shape:{" "}
                <code>{`{"error": {"code", "message", "request_id"}}`}</code>.
              </p>

              <h3>Machine API (SDK)</h3>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Endpoint</th>
                      <th>Purpose</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td className="data-table__mono">POST /intercept</td>
                      <td>
                        The core decision: allow, block, or require approval for one tool call.
                      </td>
                    </tr>
                    <tr>
                      <td className="data-table__mono">POST /spans/{"{id}"}/complete</td>
                      <td>Reports the outcome of a call the SDK executed after being allowed.</td>
                    </tr>
                    <tr>
                      <td className="data-table__mono">GET /approvals/{"{id}"}</td>
                      <td>Long-polls a pending approval until a human resolves it.</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <h3>Human/dashboard API (selected endpoints)</h3>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Endpoint</th>
                      <th>Purpose</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td className="data-table__mono">POST /auth/login, /auth/signup</td>
                      <td>Human sessions — JWT access + rotating refresh tokens.</td>
                    </tr>
                    <tr>
                      <td className="data-table__mono">/agents, /policies</td>
                      <td>Create/list agents and versioned policies; activate a policy version.</td>
                    </tr>
                    <tr>
                      <td className="data-table__mono">POST /policies/simulate</td>
                      <td>Runs a hypothetical call through the real policy engine, no side effects.</td>
                    </tr>
                    <tr>
                      <td className="data-table__mono">/traces, /traces/{"{id}"}</td>
                      <td>Trace Explorer's list/filter and Incident Replay's full event history.</td>
                    </tr>
                    <tr>
                      <td className="data-table__mono">/threats, /costs, /agents/{"{id}"}/health</td>
                      <td>Threat Center, Cost Center, and Agent Health's real aggregates.</td>
                    </tr>
                    <tr>
                      <td className="data-table__mono">/api-tokens</td>
                      <td>Long-lived personal tokens for scripts/CI to call this same API.</td>
                    </tr>
                    <tr>
                      <td className="data-table__mono">/llm-keys</td>
                      <td>Store/list/revoke your own OpenAI/Anthropic/Gemini key (BYOK, above).</td>
                    </tr>
                    <tr>
                      <td className="data-table__mono">POST /demo/live-run</td>
                      <td>
                        Runs the prompt-injection demo with a real LLM (BYOK key or local Ollama)
                        deciding which tool to call.
                      </td>
                    </tr>
                    <tr>
                      <td className="data-table__mono">WS /live/{"{agent_id}"}</td>
                      <td>Realtime node/edge deltas that drive the live 3D graph.</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p>
                The complete contract for every endpoint — request/response shapes, status codes,
                error codes — is in <code>docs/API_SPEC.md</code> in the repository.
              </p>
            </section>

            <section id="deployment" className="docs-section">
              <h2>Deployment</h2>
              <p>
                A production deployment needs, at minimum: managed Postgres, Redis, and Kafka (or
                a Kafka-compatible service), object storage for large payloads, and a{" "}
                <code>BYOK_MASTER_KEY</code> if you want the LLM-key feature enabled — without it,
                that feature fails closed (no key storage or use is possible, rather than silently
                falling back to something less safe). Every environment variable is documented in{" "}
                <code>.env.example</code> at the repository root.
              </p>
            </section>

            <section id="architecture" className="docs-section">
              <h2>Architecture, briefly</h2>
              <p>
                <strong>Interceptor</strong> makes the allow/block/approve decision against an
                in-memory policy cache and writes the decision to Postgres's append-only{" "}
                <code>events</code> table — this write is synchronous, not fire-and-forget, because
                losing the only record a call happened is a worse failure mode than a few extra
                milliseconds of latency for a system whose entire value is "every call is audited."
                A transactional outbox publishes each event to Kafka, which an{" "}
                <strong>aggregator</strong> consumes to fold events into the graph state the
                dashboard reads and fan them out over a live WebSocket. Incident Replay reconstructs
                a trace by folding its events in order, on demand — the exact same fold function
                the live graph uses, so what you see live and what you see on replay can never
                structurally disagree.
              </p>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
