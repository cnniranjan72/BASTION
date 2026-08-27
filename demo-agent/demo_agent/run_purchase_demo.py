"""Run the Track 01 commerce scenarios against a real, already-running
interceptor — same "watch it happen live" path run_demo.py uses for the
prompt-injection scenario.

Usage (from repo root, interceptor + catalog service running on :4001/:4003):
    uv run --project demo-agent python -m demo_agent.seed
    uv run --project demo-agent python -m demo_agent.run_purchase_demo
    uv run --project demo-agent python -m demo_agent.run_purchase_demo --repeat 20

Scenario A (the revenue case) and Scenario B (the anomaly case) share the
exact same `razorpay.purchase` calls_per_minute budget (buyer_agent.py's
own docstring explains why) — `--repeat` above RATE_LIMIT_PER_MINUTE
self-paces with real waits for the window to reset rather than gaming or
hiding the interaction, so a large --repeat genuinely takes real wall-clock
time (roughly repeat/3 minutes), not a shortcut.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from bastion import BastionClient

# Windows' default console codepage (cp1252) can't encode ₹ (U+20B9) —
# a real crash hit running this for the first time, not a hypothetical.
# UTF-8 stdout is the correct general fix, not stripping the currency
# symbol to stay ASCII-safe.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from .buyer_agent import (
    RATE_LIMIT_PER_MINUTE,
    run_purchase_burst_scenario,
    run_purchase_scenario,
)
from .seed import AGENT_API_KEY, AGENT_ID

INTERCEPTOR_URL = os.environ.get("INTERCEPTOR_URL", "http://localhost:4001")


def _client() -> BastionClient:
    return BastionClient(base_url=INTERCEPTOR_URL, api_key=AGENT_API_KEY, agent_id=AGENT_ID)


async def _run_scenario_a_once(run_number: int) -> bool:
    client = _client()
    try:
        result = await run_purchase_scenario(client)
    finally:
        await client.aclose()

    if result.blocked:
        print(f"[purchase {run_number}] BLOCKED ({result.block_reason})")
        return False

    receipt = result.receipt
    assert receipt is not None
    label = "simulated" if receipt["simulated"] else "REAL"
    print(
        f"[purchase {run_number}] {receipt['item_name']} x{receipt['quantity']} "
        f"-> {label} receipt: order={receipt['order_id']} payment={receipt['payment_id']} "
        f"amount=₹{receipt['amount_inr']}"
    )
    return True


async def run_scenario_a(repeat: int) -> bool:
    print(f"\n=== Scenario A: the revenue case ({repeat}x) ===")
    successes = 0
    for i in range(repeat):
        if i > 0 and i % RATE_LIMIT_PER_MINUTE == 0:
            print(
                f"  (pacing: {RATE_LIMIT_PER_MINUTE} purchases sent, waiting 61s for "
                "razorpay.purchase's calls_per_minute window to reset — real wait, not skipped)"
            )
            await asyncio.sleep(61)
        successes += await _run_scenario_a_once(i + 1)
    print(f"\n{successes}/{repeat} purchases completed successfully.")
    return successes == repeat


async def run_scenario_b(attempts: int) -> bool:
    print(f"\n=== Scenario B: the anomaly case (burst of {attempts}) ===")
    client = _client()
    try:
        result = await run_purchase_burst_scenario(client, attempts=attempts)
    finally:
        await client.aclose()

    for i, attempt in enumerate(result.attempts, start=1):
        status = "allowed" if attempt.allowed else f"BLOCKED ({attempt.reason})"
        print(f"[burst {i}] {status}")

    print(f"\n{result.allowed_count} allowed, {result.blocked_count} blocked out of {attempts}.")
    # The burst is meant to demonstrate the limit firing, not to predict an
    # exact split — see buyer_agent.py's module docstring on why the exact
    # cutover depends on whatever budget Scenario A already used.
    return result.blocked_count > 0


async def main(repeat: int, burst_attempts: int) -> int:
    print(f"Target interceptor: {INTERCEPTOR_URL}")
    a_ok = await run_scenario_a(repeat)
    # Scenario A and B share the same calls_per_minute budget (module
    # docstring) — without this wait, Scenario B would inherit whatever's
    # left of Scenario A's window (anywhere from 0 to 2 slots) instead of
    # demonstrating its own allow-then-block transition cleanly. A real
    # 61s wait, not a shortcut, same as the pacing between Scenario A's
    # own batches.
    print(
        "\n(waiting 61s so Scenario B starts with its own fresh "
        "calls_per_minute window, not whatever Scenario A left behind)"
    )
    await asyncio.sleep(61)
    b_ok = await run_scenario_b(burst_attempts)
    return 0 if (a_ok and b_ok) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repeat",
        type=int,
        default=3,
        help="run Scenario A this many times (reliability check); values above "
        f"{RATE_LIMIT_PER_MINUTE} self-pace with real ~61s waits between batches",
    )
    parser.add_argument(
        "--burst-attempts",
        type=int,
        default=6,
        help="how many rapid purchase attempts Scenario B makes",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.repeat, args.burst_attempts)))
