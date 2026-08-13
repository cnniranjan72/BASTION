# BASTION — Product Requirements Document

## 1. One-line pitch
BASTION is a control plane that sits between AI agents and the outside world — every tool call, API request, or database mutation an agent attempts is intercepted, checked against policy, allowed/blocked/escalated, and recorded — giving teams real-time control and a full causal replay of what their agents actually did.

## 2. The problem
Teams shipping LLM agents in production have three unsolved problems simultaneously:

1. **No prevention.** Today's tools (LangSmith, Helicone, etc.) log what an agent did *after* it happened. Nobody stops a prompt-injected or hallucinating agent from calling `DELETE /users` or transferring money *before* it happens.
2. **No causality.** Agent runs involve async LLM calls, tool calls, retries, and sub-agents. When something goes wrong, engineers can't reconstruct *why* — which call triggered which decision, in what order, with what inputs.
3. **No policy enforcement.** There's no equivalent of IAM for agent actions. "This agent may call payment APIs up to $50 without approval, above that needs a human" does not exist as infrastructure today — every team hand-rolls fragile guardrails inside prompts, which fail.

## 3. Who feels this pain
- Backend/platform engineers at AI-native startups running agents in production (customer support agents, coding agents, ops agents)
- Engineering leads who are personally liable when an agent does something destructive
- Security teams trying to reason about what "non-deterministic software with tool access" is allowed to do

## 4. Why now
Agents moved from demo to production in the last 12–18 months. The tooling for *building* agents (LangChain, frameworks, orchestration) matured fast. The tooling for *governing* agents in production did not. This is the current gap.

## 5. What BASTION does (v1 scope)
BASTION is a **proxy + policy engine + replay UI** for agent tool calls. It is NOT a full agent framework, NOT a prompt-engineering tool, and NOT a general APM.

### Core capabilities (v1)
1. **Interception** — Agents route tool calls (HTTP calls to external APIs, DB queries, or MCP tool invocations) through a BASTION SDK/proxy before they execute.
2. **Policy evaluation** — Each intercepted call is evaluated against a policy (written in a small declarative policy DSL, e.g. "block DELETE queries on `production` DB unless approved," "allow payment API calls under $50, escalate above").
3. **Decision** — Allow / Block / Require human approval (async, via webhook/notification).
4. **Event sourcing** — Every intercepted call, decision, and outcome is stored as an immutable event, linked by a `trace_id` + `span_id` (parent/child) so full causal chains can be reconstructed, even across retries and parallel branches.
5. **Replay** — Given a `trace_id`, reconstruct the full execution graph: which LLM call led to which tool call, what was blocked, what was retried, in what order, with what latency and cost at each step.
6. **Live 3D visualization** — A real-time force-directed 3D graph of an agent's execution as it happens: nodes = LLM calls / tool calls / decisions, edges = causal/temporal relationships, color = allow/block/pending, size = latency or cost. This is the single most demoable piece of the product.
7. **Cost & anomaly tracking** — Aggregate token/API cost per trace, per agent, per policy; flag anomalies (sudden cost spike, unusual call pattern) as a byproduct of already-captured event data.

### Explicitly out of scope for v1
- Building/orchestrating the agents themselves (bring-your-own-agent, framework-agnostic)
- Fine-tuning or prompt optimization
- Multi-cloud deployment tooling (single deployment target for v1: containerized, cloud-agnostic but demoed on one)

## 6. Success criteria (how you know it's "done" and good)
- A demo agent (you'll build a small reference agent) with real tool access (DB + one external API) runs through BASTION.
- You can inject a "malicious" instruction (simulated prompt injection) and show BASTION blocking the resulting dangerous tool call **before** it executes, live, in the 3D view.
- You can pull up any past trace and fully replay it — see every decision node, timing, and payload.
- p99 interception latency overhead is measured and documented (target: <50ms added per intercepted call — this number matters, know it cold in the interview).
- Policies are hot-reloadable without redeploying the proxy.

## 7. Key technical challenges (this is your interview story)
1. **Causality tracking across async, non-deterministic execution** — solved via distributed tracing concepts (trace_id/span_id propagation, happens-before ordering) applied to a domain (LLM agents) where OpenTelemetry conventions don't cleanly fit.
2. **Low-latency synchronous interception** — the proxy sits in the hot path; policy evaluation must be fast (in-memory policy cache, no DB round-trip in the common case).
3. **Event sourcing + replay** — append-only event log as source of truth; the "current state" of any trace is a fold over its events, not a mutable row.
4. **Real-time fan-out to a 3D UI** — server-sent events or WebSockets pushing graph deltas to a client maintaining a live force-directed layout, at production-grade frame stability (not a toy visualization that jitters).
5. **Async human-approval flow** — a blocked call can pause execution (via long-poll or webhook callback) pending a human decision, then resume — this is a real distributed workflow problem (durable execution).

## 8. Personas
- **Priya, platform engineer at an AI startup** — needs to sleep at night knowing the support agent can't refund $10,000 by accident.
- **Sam, eng lead** — needs to debug why an agent looped 40 times and burned $200 in API costs, without reading raw logs.
- **Security reviewer** — needs an audit trail proving what an agent could and couldn't do, for compliance.
