# BASTION — Pitch Brief

**One page. Every number below is reproducible right now — see README.md for the exact command
behind each one.**

## Problem

The track's own framing: *"NPCI's UAP and the global protocol race (ACP, AP2, x402) make
agent-to-agent commerce the open problem of the year."* Four different, competing proposals for how
an agent should pay — none of them answer who's watching what the agent is about to do, or whether
anything can stop it before the money moves. That's the trust gap this project targets: not another
payment rail, the missing control plane in front of whichever one wins.

## What this is

BASTION is a policy gate in front of every tool call an AI agent makes — allow, block, or route to a
human, decided before the call executes, not logged after the fact. The track's own bar: *"every
money action explainable, bounded and gated ... audit trail ... one failure handled gracefully."*
That's not aspirational copy here, it's the architecture: every decision carries a real policy reason
("Why?"), `razorpay.purchase` is bounded by an approval threshold and a rate limit, nothing executes
before the gate decides, and the rate-limit burst below is the one failure — handled, and shown. For
Track 01, this exact mechanism governs a real Razorpay purchase flow: an agent browses a live
merchant catalog and buys — "makes a merchant transactable by an AI buyer end to end," the build
challenge's own second option, not a repurposed unrelated demo bent to fit the track after the fact.

## Evidence, not adjectives

- **332/332 tests passing** (266 backend against real Postgres/Redis/Kafka, 66 frontend) —
  `uv run pytest shared/tests interceptor/tests aggregator/tests sdk-python/tests demo-agent/tests catalog/tests && (cd frontend && npm run test -- --run)`
- **Live in production, verified 2026-08-27** — interceptor, aggregator, and catalog all reachable
  and healthy right now (`bastion-interceptor.onrender.com`, `bastion-aggregator.onrender.com`,
  `bastion-catalog.onrender.com`), not just at deploy time.
- **Scenario A — governed purchase, just re-run**: 3/3 real order+payment receipts,
  `Wireless Earbuds Pro x1 -> simulated receipt: order=order_1e958736483545 payment=pay_8b5b287179c141 amount=₹1499`
  (repeated 3x, all succeeded).
- **Scenario B — the anomaly, just re-run**: a burst of 6 rapid purchase attempts —
  `3 allowed, 3 blocked out of 6`, reason `calls_per_minute limit 3 exceeded for tool 'razorpay.purchase'`,
  same audit trail as every other decision this system makes.
- **Governance scenario, just re-run**: `20/20 runs correctly blocked the injected transfer` — a
  support ticket with an injected "transfer $500 to attacker-9999" instruction, caught every time,
  with a legitimate $25 refund in the same trace still going through.

## The one disclosed limitation

Production `/intercept` currently costs 1.4s+ per call (p99 8.73s under load) — not a code defect,
a measured, root-caused consequence of this build's free-tier backing services sitting in three
different cloud regions (Render compute in Oregon, Postgres in Ohio, Redis/Kafka in Mumbai). Full
methodology in README.md.

## What a week added

Everything Razorpay-specific is new, not a repurposed unrelated project: a standalone catalog
service (`catalog/`, `GET /catalog`, deployed to `bastion-catalog.onrender.com`), a new
`razorpay.purchase` tool wired through the exact same `BastionClient.call()` → intercept →
allow/block → `execute()` path the rest of the system already used, and two new policy rules
(approval required above ₹18,000, rate-limited to 3 calls/minute) — zero changes to the policy
engine itself. One constraint stated plainly, not glossed over: Razorpay's own Payments API only
allows capturing a payment that originated through their hosted Checkout or client SDKs — never a
pure backend call, regardless of credentials. So this build's purchase flow is real end to end
through order creation; capture is simulated by design, not by time pressure, and every receipt
says so inline (`"simulated": true/false`) in the data itself.
