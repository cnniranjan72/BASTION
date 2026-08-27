import { Link } from "react-router-dom";
import {
  AgentsIcon,
  AlertIcon,
  ApprovalsIcon,
  GraphIcon,
  PoliciesIcon,
  ThreatIcon,
} from "./icons";

// Kept in sync by hand, not injected at build time (a cross-language pytest+Vitest
// counter was judged not worth the added build-time failure surface this close to
// submission). Source of truth: `uv run pytest shared/tests interceptor/tests
// aggregator/tests sdk-python/tests demo-agent/tests catalog/tests` (266) +
// `cd frontend && npm run test -- --run` (66) = 332. Must match README.md's opening
// table and docs/PITCH_BRIEF.md — if you change one, change all three.
const PROOF_POINTS = [
  { value: "332", label: "tests passing — 266 against real Postgres/Redis/Kafka, 66 frontend" },
  { value: "0", label: "dangerous calls that ever reach a real API — blocked before execute()" },
  { value: "3", label: "new policy rules, zero changes to the policy engine itself" },
];

const HOW_IT_WORKS = [
  {
    icon: GraphIcon,
    title: "Every call, intercepted",
    body: "An agent routes a tool call through the BASTION SDK — the connector code it calls instead of reaching the outside world directly. It's checked against policy before it runs — not logged after the fact.",
  },
  {
    icon: PoliciesIcon,
    title: "A decision, in milliseconds",
    body: "An in-memory policy cache decides allow, block, or route to a human — no database round-trip on the hot path.",
  },
  {
    icon: ApprovalsIcon,
    title: "Explainable, always",
    body: "Every decision is a real, append-only event — a permanent record only ever added to, never edited or deleted: the exact args, the policy that fired, the reason — retrievable per trace, not summarized away.",
  },
];

export function LandingPage() {
  return (
    <div className="landing">
      <header className="landing__nav">
        <span className="landing__wordmark">
          <span className="landing__wordmark-dot" /> BASTION
        </span>
        <nav className="landing__nav-links">
          <a href="#how-it-works">How it works</a>
          <a href="#see-it-work">See it work</a>
          <Link to="/login">Sign in</Link>
          <Link to="/signup" className="landing__nav-cta">
            Get started
          </Link>
        </nav>
      </header>

      <section className="landing__hero">
        <p className="landing__plain-statement">
          This stops an AI agent from making a dangerous payment before it happens — watch it
          block a real attack below.
        </p>
        <span className="landing__eyebrow">AI agent control plane</span>
        <h1 className="landing__headline">
          Stop a dangerous agent call
          <br />
          before it executes — not after.
        </h1>
        <p className="landing__subhead">
          BASTION sits between an AI agent and the outside world. Every tool call — a payment, a
          database mutation, a purchase — is checked against policy, allowed, blocked, or routed
          to a human, and recorded as an immutable event. Real prevention, not after-the-fact
          logging.
        </p>
        <div className="landing__cta-row">
          <Link to="/demo" className="btn btn--primary btn--lg">
            See it block an attack
          </Link>
          <Link to="/signup" className="btn btn--ghost btn--lg">
            Create your organization
          </Link>
          <a
            href="https://github.com/cnniranjan72/BASTION"
            target="_blank"
            rel="noreferrer"
            className="btn btn--ghost btn--lg"
          >
            View the source
          </a>
        </div>

        <div className="landing__proof-strip">
          {PROOF_POINTS.map((p) => (
            <div className="landing__proof" key={p.label}>
              <span className="landing__proof-value">{p.value}</span>
              <span className="landing__proof-label">{p.label}</span>
            </div>
          ))}
        </div>

        <figure className="landing__demo-shot">
          <img
            src="/live-graph-demo.gif"
            alt="The BASTION live execution graph, showing real razorpay.purchase nodes and a real span inspector opened on a completed call"
            width={960}
            height={463}
            loading="lazy"
          />
          <figcaption>
            The live execution graph, mid-session — every node is a real intercepted call, not a
            mockup.
          </figcaption>
        </figure>
      </section>

      <section className="landing__section" id="how-it-works">
        <div className="landing__section-head">
          <span className="landing__eyebrow">How it works</span>
          <h2>The hot path — the split second a call gets decided — in three steps</h2>
        </div>
        <div className="landing__steps">
          {HOW_IT_WORKS.map((step, i) => (
            <div className="landing__step" key={step.title}>
              <div className="landing__step-top">
                <step.icon className="landing__step-icon" width={22} height={22} />
                <span className="landing__step-num">{String(i + 1).padStart(2, "0")}</span>
              </div>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="landing__section landing__section--dark" id="see-it-work">
        <div className="landing__section-head">
          <span className="landing__eyebrow">See it work</span>
          <h2>Two scenarios, run for real</h2>
          <p className="landing__section-sub">
            Not screenshots of a happy path — real terminal output from a real local run, against
            a real interceptor and a real policy engine.
          </p>
        </div>

        <div className="landing__scenarios">
          <div className="landing__scenario">
            <div className="landing__scenario-head">
              <AlertIcon className="landing__scenario-icon landing__scenario-icon--danger" width={20} height={20} />
              <h3>Governance: prompt injection, blocked</h3>
            </div>
            <p>
              A support ticket contains an injected instruction: transfer $500 to an attacker
              account. The agent acts on it — same as a real LLM could be tricked into doing.
            </p>
            <pre className="landing__terminal">
{`[run 1] injected $500 transfer -> BLOCKED (correct)
  (blocked by policy rule for tool 'payments.transfer')
[run 1] legitimate $25 refund -> sent

20/20 runs correctly blocked the injected transfer.`}
            </pre>
          </div>

          <div className="landing__scenario">
            <div className="landing__scenario-head">
              <AgentsIcon className="landing__scenario-icon" width={20} height={20} />
              <h3>Commerce: an AI buyer transacts, then goes rogue</h3>
            </div>
            <p>
              An AI buyer agent browses a real product catalog, picks an item, and completes a
              purchase. The same policy also catches the agent when a bug or an injected loop
              tries to buy repeatedly.
            </p>
            <pre className="landing__terminal">
{`[purchase 1] Wireless Earbuds Pro x1 -> receipt: order=order_577a5f... amount=₹1499
3/3 purchases completed successfully.

[burst 1] allowed
[burst 2] allowed
[burst 3] allowed
[burst 4] BLOCKED (calls_per_minute limit 3 exceeded)`}
            </pre>
          </div>
        </div>
      </section>

      <section className="landing__section">
        <div className="landing__section-head">
          <span className="landing__eyebrow">The gap</span>
          <h2>There's no IAM (the "who's allowed to do what" rules) for agent actions — yet</h2>
          <p className="landing__section-sub">
            Today's observability tools log what an agent did after it happened. Nobody stops a
            hallucinating or prompt-injected agent from calling a dangerous tool before it
            executes. "This agent may call payment APIs up to $50 without approval, above that
            needs a human" doesn't exist as infrastructure. BASTION is that layer.
          </p>
        </div>
      </section>

      <section className="landing__final-cta">
        <ThreatIcon className="landing__final-cta-icon" width={28} height={28} />
        <h2>Bring your own agent, watch it live.</h2>
        <p>
          No credit card, no sales call — a real org and a real policy, set up in under a minute.
          Don't have an agent to connect yet? <Link to="/demo">See a real one work right now</Link>{" "}
          instead.
        </p>
        <Link to="/signup" className="btn btn--primary btn--lg">
          Create your organization
        </Link>
      </section>

      <footer className="landing__footer">
        <span>BASTION — an AI agent control plane.</span>
        <a href="https://github.com/cnniranjan72/BASTION" target="_blank" rel="noreferrer">
          GitHub
        </a>
      </footer>
    </div>
  );
}
