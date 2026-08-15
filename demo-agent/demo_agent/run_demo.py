"""Run the prompt-injection demo scenario against a real, already-running
interceptor (not ASGITransport — this is the "watch it happen live" path,
meant to be run against the same interceptor+Postgres the Phase 7 frontend
is pointed at, so the blocked call shows up red in the live graph).

Usage (from repo root, interceptor running on :4001):
    uv run --project demo-agent python -m demo_agent.seed
    uv run --project demo-agent python -m demo_agent.run_demo
    uv run --project demo-agent python -m demo_agent.run_demo --repeat 20

Optional real-LLM backend (docs/adr/ADR-022) instead of the deterministic
stand-in — nondeterministic, so not used for --repeat reliability runs:
    uv run --project demo-agent python -m demo_agent.run_demo --llm ollama
    LLM_API_KEY=sk-... uv run --project demo-agent python -m demo_agent.run_demo --llm openai
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from bastion import BastionClient

from .agent import run_prompt_injection_scenario
from .llm_agent import run_llm_backed_scenario
from .seed import AGENT_API_KEY, AGENT_ID

INTERCEPTOR_URL = os.environ.get("INTERCEPTOR_URL", "http://localhost:4001")


async def _run_once(run_number: int, *, llm: str | None) -> bool:
    client = BastionClient(base_url=INTERCEPTOR_URL, api_key=AGENT_API_KEY, agent_id=AGENT_ID)
    try:
        if llm is not None:
            api_key = None if llm == "ollama" else os.environ.get("LLM_API_KEY")
            result = await run_llm_backed_scenario(client, provider=llm, api_key=api_key)
        else:
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


async def main(repeat: int, *, llm: str | None) -> int:
    print(f"Target interceptor: {INTERCEPTOR_URL}")
    backend = f"real LLM ({llm})" if llm is not None else "deterministic stand-in"
    print(f"Running the prompt-injection scenario {repeat} time(s) [{backend}]...\n")
    results = [await _run_once(i + 1, llm=llm) for i in range(repeat)]
    blocked_count = sum(results)
    print(f"\n{blocked_count}/{repeat} runs correctly blocked the injected transfer.")
    return 0 if blocked_count == repeat else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repeat", type=int, default=1, help="run the scenario this many times (reliability check)"
    )
    parser.add_argument(
        "--llm",
        choices=["ollama", "openai", "anthropic", "gemini"],
        default=None,
        help="use a real LLM instead of the deterministic stand-in (nondeterministic; "
        "LLM_API_KEY env var required for non-ollama providers)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.repeat, llm=args.llm)))
