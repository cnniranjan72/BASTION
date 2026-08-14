# Bastion — Frontend v2 (Control Plane UI, not a dashboard)

Supersedes the frontend section of ARCHITECTURE.md §2.6. v1 gave you a live graph and a plain dashboard. v2 makes the frontend prove the backend's capabilities are real — every screen should be backed by the actual mechanisms in UPGRADE_ARCHITECTURE.md, never a mocked state.

## Positioning
Not "a dashboard for an agent gateway." Think **Linear × Vercel × Cloudflare × Datadog**, purpose-built for AI agent governance: near-black/charcoal palette, dense information, restrained accent color, status colors used only when semantically meaningful, monospace for IDs/logs, keyboard-first navigation, excellent empty states. No dark-mode-purple-gradient-glowing-AI-blob aesthetic — this is infrastructure/security software, not a consumer AI toy.

## The three flagship experiences (build these well; everything else supports them)

### 1. Live Execution Graph (the centerpiece)
Not "cool 3D nodes floating" — an actual execution debugger.
- Real-time force-directed graph of the current agent run: Agent → Model Call → Tool Calls, branching on parallel calls, each node colored by state (per the state machine in UPGRADE_ARCHITECTURE.md §2): grey=pending, green=allowed/completed, red=blocked, amber=pending approval.
- Click a node → inspector panel opens with the full decision context: agent, trace_id, policy matched, decision, exact reason ("amount = $500, maximum allowed = $100"), latency, timestamp, payload (fetched from object storage if offloaded, per §12).
- **Timeline strip** synchronized with the graph: a horizontal event timeline below the graph; clicking a timeline entry highlights the corresponding node, and clicking a node scrolls/highlights the timeline. This pairing is what turns "a graph" into "a debugger."
- Must consume the real WebSocket fan-out (§13) — verify it under the chaos test where a WS connection drops mid-session and correctly resyncs full current state, not just future deltas.

### 2. Policy Studio
Not a CRUD form. A structured, visual rule builder over the same policy DSL from ARCHITECTURE.md §2.3 and UPGRADE_ARCHITECTURE.md §8:
- `WHEN <agent> calls <tool> IF <condition> THEN <action>` composed from dropdowns/structured inputs, compiling to the same YAML the interceptor consumes — the UI is a front-end for the real DSL, not a separate parallel concept.
- **Policy simulator**: paste a hypothetical tool call (tool, agent, args) and see it walk through the actual evaluation chain (auth → agent identity → policy match → limits check → decision) using the real policy engine, not a UI-only approximation.
- **Version diff view**: show `v12 → v13` field-by-field changes (e.g. `MAX_AMOUNT: $100 → $250`), who changed it, when, and which agents are affected — backed by the optimistic-concurrency versioning in UPGRADE_ARCHITECTURE.md §5.
- On save, show real propagation status pulled from actual interceptor health/version checks: "Policy v14 active across 4/4 interceptors" — not a generic "Saved ✓". This is the detail that proves the UI is wired to real distributed state, not faked.

### 3. Incident Replay
The feature that only exists because the backend is properly event-sourced (DATA_MODEL.md + outbox in UPGRADE_ARCHITECTURE.md §4).
- Pick a past trace/incident, hit replay, and watch the execution graph animate through the exact original sequence with real timestamps: `00:00.210 Prompt injection detected in content` → `00:00.854 Agent requests payments.transfer` → `00:00.862 BLOCKED`.
- This is reconstructed purely from the immutable `events` table — no separate "replay data" storage. If replay can't be derived from the event log alone, the event log isn't actually the source of truth, and that's a backend bug to fix, not a frontend workaround to build.

## Supporting surfaces

### Command Center (home/overview)
Live, not refresh-based — driven by the same WebSocket fan-out as the graph:
```
● 12 agents healthy   ● 99.97% availability   Last incident 14m ago

LIVE AGENT ACTIVITY
● support-agent   payments.transfer   BLOCKED
● refund-agent    customers.lookup    ALLOWED
● sales-agent     send_email          APPROVAL
```

### Trace Explorer
Jaeger/Datadog-style search over traces: filter by agent, status, tool, policy, time range. Each result expands to the full replay (reuses Incident Replay).

### Approval Center
Framed as an incident queue, not a passive list — each pending approval shows the causal trace leading up to it so the approver has context before deciding:
```
⚠ HIGH RISK APPROVAL
payments.transfer — billing-agent — $742.00
Policy: payments-v4 — exceeds automatic threshold
[ DENY ]   [ APPROVE ]
```

### Threat Center
Surfaces the security-relevant aggregates that fall out of the event log for free: blocked-action counts, detected prompt-injection patterns, top violated policies, a threat timeline. This is where Bastion visibly earns the "governance/security product" framing rather than just "API gateway."

### Agent Detail + Agent Health
Per-agent stats (calls, blocked, approvals, avg latency, estimated cost, top tools by volume) plus a derived "health" score (reliability, policy compliance, tool error rate, approval rate, cost efficiency) — computed from real aggregated event data, with anomaly flags ("tool-call frequency increased 4.7× over baseline") backed by the actual rate/behavior metrics from UPGRADE_ARCHITECTURE.md §14, not hardcoded thresholds shown for demo purposes.

### Cost Center
Aggregated spend by agent/tool/category (LLM cost, tool cost, external API cost), computed from the `estimated_cost_total` metric and per-call cost data already captured in events — plus an "estimated savings from policy enforcement" figure (sum of cost that would have been incurred by blocked/denied calls). This directly demonstrates the cost-governance policy extension (§8) has real teeth.

### Command palette (⌘K)
Genuinely useful, not decorative: jump to any agent/trace/policy, show blocked calls, show pending approvals, replay last incident, create a policy — all real navigation/actions, not a static list.

### "Why?" everywhere
Any decision surface (blocked, allowed, approved, rate-limited) gets a "Why?" affordance that opens the exact policy match + condition + input values that produced it — this is the UI expression of the explicit policy evaluation chain in UPGRADE_ARCHITECTURE.md §9, and it should be one shared component, not reimplemented per screen.

## Navigation structure
```
Overview

OPERATE      Live · Traces · Incidents · Approvals
CONTROL      Agents · Policies · Policy Simulator
SECURITY     Threats · Audit Log
ANALYZE      Analytics · Costs · Agent Health
ADMIN        Team · API Keys · Settings
```

## Non-negotiable rule
Every number, status, and decision shown in the UI must trace back to a real backend mechanism from ARCHITECTURE.md/UPGRADE_ARCHITECTURE.md. If a screen can't be wired to something real yet (e.g. Threat Center before the security event aggregation exists), build the screen after its backend dependency, not before — with a clear "not yet backed" note in PROGRESS.md rather than a plausible-looking mock. This mirrors CLAUDE.md's standing rule against mock data past Phase 4, extended explicitly to the UI.
