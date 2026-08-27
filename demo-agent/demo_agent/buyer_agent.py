"""Track 01: an AI buyer agent calling `razorpay.purchase` through
`BastionClient.call()` — the identical pattern `agent.py`'s
`payments.transfer` scenario already uses (agent -> call() -> intercept
-> allowed/blocked -> execute() only on allow).

Two scenarios, both real, both built on `buy_item()` below:
  - `run_purchase_scenario`: the revenue case — browse the catalog, pick
    an item, buy it, get a real (labeled) receipt back.
  - `run_purchase_burst_scenario`: the anomaly case — an agent gone wrong
    (bug, injection, bad logic) attempts a rapid burst of purchases.
    `razorpay.purchase`'s own `calls_per_minute` limit (seed.py) is what
    catches this, not the amount threshold — the oversized-single-purchase
    path (`require_approval` above ₹18,000) is real and interceptor-tested
    (interceptor/tests) rather than run live here, since resolving a
    pending approval needs either a real human or a ~25-60s fail-closed
    timeout with nobody there to approve it — a bad look live, and not
    what "an agent gone wrong" is actually about anyway.

RATE_LIMIT_PER_MINUTE below must match seed.py's own
`{"calls_per_minute": 3}` — both scenarios share the exact same Redis key
(`bastion:limits:calls:{agent_id}:razorpay.purchase`, limits.py), so
`run_purchase_scenario`'s own calls count against the same budget
`run_purchase_burst_scenario` is trying to exhaust. That's not a bug to
work around, it's the real mechanism working exactly as designed — this
module reports whatever the real counts turn out to be rather than
assuming a clean slate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bastion import BastionBlockedError, BastionClient

from . import razorpay_tools

PRIMARY_SKU = "EARBUDS-PRO"
BURST_SKU = "CHARGER-65W"
RATE_LIMIT_PER_MINUTE = 3  # seed.py's POLICY_DEFINITION: razorpay.purchase limits.calls_per_minute


async def buy_item(client: BastionClient, *, sku: str, quantity: int = 1) -> dict[str, Any]:
    catalog = await razorpay_tools.fetch_catalog()
    item = next((i for i in catalog if i["sku"] == sku), None)
    if item is None:
        raise ValueError(f"no catalog item with sku {sku!r}")

    price_inr = item["price_inr"]
    name = item["name"]
    return await client.call(
        "razorpay.purchase",
        {"sku": sku, "quantity": quantity, "amount_inr": price_inr * quantity},
        lambda: razorpay_tools.purchase(sku=sku, name=name, price_inr=price_inr, quantity=quantity),
    )


@dataclass
class PurchaseResult:
    receipt: dict[str, Any] | None = None
    blocked: bool = False
    block_reason: str | None = None


async def run_purchase_scenario(client: BastionClient, *, sku: str = PRIMARY_SKU) -> PurchaseResult:
    """Scenario A, the revenue case: an AI buyer agent browses the
    catalog (a real GET /catalog call inside buy_item -> fetch_catalog),
    picks an item, and completes a purchase."""
    result = PurchaseResult()
    try:
        result.receipt = await buy_item(client, sku=sku, quantity=1)
    except BastionBlockedError as exc:
        # Only reachable if the shared rate-limit window (see module
        # docstring) was already exhausted by other calls — reported
        # honestly rather than treated as impossible.
        result.blocked = True
        result.block_reason = exc.reason
    return result


@dataclass
class BurstAttempt:
    allowed: bool
    reason: str | None


@dataclass
class BurstResult:
    attempts: list[BurstAttempt] = field(default_factory=list)

    @property
    def allowed_count(self) -> int:
        return sum(1 for a in self.attempts if a.allowed)

    @property
    def blocked_count(self) -> int:
        return sum(1 for a in self.attempts if not a.allowed)


async def run_purchase_burst_scenario(
    client: BastionClient, *, sku: str = BURST_SKU, attempts: int = 6
) -> BurstResult:
    """Scenario B, the anomaly case: a rapid burst of purchase attempts,
    simulating an agent gone wrong rather than one legitimate buy.
    `attempts` defaults to 6 — comfortably past RATE_LIMIT_PER_MINUTE
    even if some budget was already consumed by Scenario A in the same
    window, so the burst reliably demonstrates the block regardless of
    exactly how many slots were left when it started."""
    result = BurstResult()
    for _ in range(attempts):
        try:
            await buy_item(client, sku=sku, quantity=1)
            result.attempts.append(BurstAttempt(allowed=True, reason=None))
        except BastionBlockedError as exc:
            result.attempts.append(BurstAttempt(allowed=False, reason=exc.reason))
    return result
