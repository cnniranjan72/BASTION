# BASTION — 5-minute pitch video script

Timed, stage-directed. Read once for pacing before recording — spoken pace runs slower than
reading pace, so if it feels short reading silently, it's probably about right out loud.

Checked against the live product and this repo's real history before being committed here — two
real errors from the original draft were caught and fixed in this pass (see the bottom of this
file for what changed and why). Everything else in the original draft held up.

---

## [0:00–0:25] Hook — cold open on the live product, not a slide

*(Screen: load `bastion-frontend.onrender.com/demo` live, cold, no narration for the first 3
seconds — let the graph auto-play. Verified live in production before this script was committed.)*

**Say:** "This is a real AI agent trying to steal money. Watch the node turn red."

*(Let it play through — blocked node, reason chip, the allowed refund going through right after.)*

**Say:** "That call never reached a payment API. It was stopped before it executed — not logged
after the fact. That's the whole idea, and everything after this is proof it's real, not a demo
shell."

## [0:25–1:00] Problem, tied directly to the track's own framing

**Say:** "Agents are starting to move money on their own — payments, purchases, refunds. The
tooling for building agents matured fast. The tooling for governing them didn't. Every serious
protocol racing to solve agent-to-agent commerce right now — NPCI's UAP, ACP, AP2, x402 — has the
same missing piece: none of them answer who's watching what the agent is about to do, or whether
anything can stop it before the money moves. That's the gap this fills."

## [1:00–1:55] Scenario A — the revenue case, live

*(Screen: terminal, run the purchase demo command live. Moved up from 1:20 — a judge skimming
only the first minute needs to already be unambiguously looking at a commerce demo, not just a
governance one.)*

**Say:** "Here's the AI buyer agent completing a real purchase. It browses a catalog, picks a
product, and the purchase goes through the same policy gate every call here goes through."

*(Let the 3 purchases print. Point at the amounts and receipt IDs.)*

**Say:** "Order creation is real, against a real endpoint. Payment capture is simulated, and I
want to say exactly why: Razorpay's own API only allows capture through their hosted checkout or
client SDK — never a pure backend call, on any platform, with any credentials. That's not a
shortcut I took, it's a constraint of the platform, and every receipt says so inline, in the data
itself."

## [1:55–2:50] Scenario B — the anomaly case, live

*(Screen: terminal, run the burst demo)*

**Say:** "Same policy engine catches an agent going wrong. Here's a burst of purchase attempts —
the rate limit blocks it after three, live, with the real reason in the audit trail."

*(Point at the block lines and the JSON reason.)*

*(Screen: switch to the browser, Trace Explorer, filter by `razorpay.purchase` — the track's bar
says "show the audit trail," so show it, don't just say it. Click into one of the blocked burst
traces to open the real inspector: real reason, real args, right there.)*

**Say:** "That's the bar this track asks for — every money action explainable, bounded, gated,
with an audit trail. Both halves, live, right now — and here it is, not just claimed."

## [2:50–3:10] Honest framing on what's new vs. what existed

*(Moved from 1:00 — after both live demos instead of before them, so this now reads as "here's
why I could build all of that," not as a delay before the viewer sees any product at all.)*

**Say:** "I want to be upfront about something: I didn't build this from scratch this week.
Earlier in this same build, before I'd even picked a track, I'd already built and deployed the
governance core — the policy engine, the audit trail, the block-before-execute mechanism. When I
read Track 01's bar, I recognized it was describing infrastructure I'd already solved. So this
final stretch, I built the actual commerce layer you just watched on top of it: a real merchant
catalog, an AI buyer agent, and a real Razorpay purchase flow, governed by the same engine."

## [3:10–3:50] Evidence, fast — numbers, not adjectives

*(Screen: README's evidence table, or terminal running the test suite)*

**Say:** "332 tests passing, real Postgres, Redis, and Kafka, no mocks. Verified live against
production this week."

*(Stage direction, real fix from the original draft — see notes below: don't recite a
memorized "about three seconds." Either run a live curl against
`bastion-interceptor.onrender.com/healthz` or a real `/intercept` call on screen and read the
actual number, or say the documented one exactly:)*

**Say:** "And I'll be honest about one number: a single call against production right now takes
about 1.4 seconds — not a bug, a disclosed infrastructure tradeoff. My compute is in Oregon, my
database is in Ohio, my Redis and Kafka are in Mumbai — I found that, measured it, and I'm telling
you instead of hiding it."

## [3:50–4:25] The judgment moment — this is the differentiator, don't rush it

**Say:** "One more thing, because I think it matters more than any feature: while reconciling test
counts this week, I found a number I could have rounded up and moved on from. Instead I traced it
to the actual commit, proved nine of the ten new tests, and reported the honest gap instead of a
tidier number nobody would have checked. That's the standard I held this whole build to — not
'does it look done,' but 'does it actually hold up if someone checks.'"

## [4:25–4:55] Close — what this means for a merchant, plainly

**Say:** "For a merchant, this means an AI agent can be trusted to actually transact — buy, refund,
upsell — because every action it takes is bounded by a rule a human wrote, and every action is
provable after the fact. That's the missing trust layer underneath agentic commerce, not another
checkout bot."

## [4:55–5:05] Sign-off

**Say:** "Everything you just watched is live, right now, at that link. Thanks for watching."

---

## Delivery notes (not content)

- **Don't read this word-for-word on camera** — know the beats, speak it naturally, or it'll
  sound rehearsed in a way that undercuts the "honest, not polished" tone that's the actual edge
  here.
- **Cut ruthlessly if you run long.** The reorder below already pushes total runtime to about
  5:05 — if something has to go, cut from the evidence section (3:10–3:50), not the two live
  demos, the Trace Explorer cut, or the judgment moment — those are what make this submission
  different from everyone else's.
- **Do one full timed dry run** before the real recording. 5 minutes goes faster than people
  expect once you're pointing at screens and waiting for terminal output.

## What was checked and changed before this was committed

This script was drafted without access to this session's own work, so two things in the original
draft didn't match the real, current state of the product and repo. Both are fixed above.

1. **The `/demo` route didn't exist in production when this script was received.** The opening
   beat's very first instruction — load `bastion-frontend.onrender.com/demo` — would have 404'd
   (technically served a blank page: the SPA returns `200` for any path, so the failure wasn't
   even visible as an HTTP error, just a black screen). Confirmed by loading it directly before
   touching anything else. Fixed by committing and deploying the `/demo` feature (already built
   and approved earlier this session) and re-verifying the live URL after Render's deploy
   completed — screenshotted, the graph auto-plays correctly now.
2. **The "before this buildathon existed" claim in the honest-framing beat was checked against this
   repo's actual git history and confirmed inaccurate.** The entire repo spans exactly 13
   continuous days (first commit 2026-08-14, the Track 01 commerce commit landing on 2026-08-27,
   the same day this check happened), 70 commits, no reference anywhere in the docs to prior work
   predating this window. Asked directly rather than guessed or silently rewritten — confirmed the
   governance core was genuinely built earlier in this same 13-day build, before Track 01 was
   picked, not before the buildathon existed. Reworded to say exactly that.
3. **The "about three seconds" production-latency figure wasn't sourced to anything documented.**
   This session's own measurements this week found two real numbers: a controlled, five-run
   sequential benchmark at a tight ~1.4s, and p95/p99 of 8.05s/8.73s under a 15 RPS load test
   (`README.md`, "Real numbers, not descriptive claims"). "About three seconds" didn't match
   either. Replaced with the documented 1.4s figure, and added a stage direction recommending an
   actual live call during recording instead of a memorized number — more convincing on camera,
   and removes any risk of reciting a number that's drifted by the time this is actually filmed.

## What changed in the adversarial pass after that

An adversarial "would this actually win" read (not a "does it work" check) found two real
pacing/proof gaps, scored against a judge who only watches the video and skims the repo for a
few minutes, not one who reads every doc:

4. **Commerce content didn't appear until 1:20 of a 5-minute video.** The first minute was
   entirely governance (the hook, the protocol-race framing) — a judge skimming only the opening
   could reasonably wonder whether this was even a Track 01 commerce submission before the video
   answered that. Fixed by reordering, not rewriting: the honest-framing beat (governance-core
   history) moved from before both live demos to after them, which also reads better narratively
   ("here's why I could build all of that" beats "trust me, more is coming"). Scenario A now
   starts at 1:00 instead of 1:20 — inside the 45–60s window a skimming judge needs.
5. **The track's bar says "show the audit trail." The script only ever said it, three times,
   never showed it.** Added one explicit cut during Scenario B: after the burst-block terminal
   output, switch to the browser, filter Trace Explorer by `razorpay.purchase`, open one blocked
   trace's real inspector. The spoken line right after it now says "and here it is, not just
   claimed" to anchor the visual to the claim.

Net effect: total runtime moved from ~5:00 to ~5:05 (the new cut adds real seconds); the
existing "cut from evidence, not the demos" guidance already covers that overage.
