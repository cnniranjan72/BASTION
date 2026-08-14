"""Run the prompt-injection demo scenario against a real, already-running
interceptor (not ASGITransport — this is the "watch it happen live" path,
meant to be run against the same interceptor+Postgres the Phase 7 frontend
is pointed at, so the blocked call shows up red in the live graph).

Usage (from repo root, interceptor running on :4001):
    uv run --project demo-agent python -m demo_agent.seed
    uv run --project demo-agent python -m demo_agent.run_demo
    uv run --project demo-agent python -m demo_agent.run_demo --repeat 20
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from bastion import BastionClient

from .agent import run_prompt_injection_scenario
from .seed import AGENT_API_KEY, AGENT_ID

INTERCEPTOR_URL = os.environ.get("INTERCEPTOR_URL", "http://localhost:4001")


async def _run_once(run_number: int) -> bool:
    client = BastionClient(base_url=INTERCEPTOR_URL, api_key=AGENT_API_KEY, agent_id=AGENT_ID)
    try:
        result = await run_prompt_injection_scenario(client)
    finally:
        await client.aclose()

    ok = result.injected_transfer_attempted and result.injected_transfer_blocked
    status = "BLOCKED (correct)" if ok else "NOT BLOCKED (BUG)"
    print(f"[run {run_number}] injected $500 transfer -> {status}", end="")
    if result.block_reason:
        print(f"  ({result.block_reason})")
    else:
        print()
    if result.legit_refund is not None:
        transfer_id = result.legit_refund["transfer_id"]
        print(f"[run {run_number}] legitimate $25 refund -> sent ({transfer_id})")
    return ok


async def main(repeat: int) -> int:
    print(f"Target interceptor: {INTERCEPTOR_URL}")
    print(f"Running the prompt-injection scenario {repeat} time(s)...\n")
    results = [await _run_once(i + 1) for i in range(repeat)]
    blocked_count = sum(results)
    print(f"\n{blocked_count}/{repeat} runs correctly blocked the injected transfer.")
    return 0 if blocked_count == repeat else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repeat", type=int, default=1, help="run the scenario this many times (reliability check)"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.repeat)))
