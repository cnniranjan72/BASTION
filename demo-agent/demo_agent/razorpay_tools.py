"""Track 01: the `razorpay.purchase` tool an AI buyer agent calls to actually
transact against a merchant's catalog. Same disclosure convention as
`tools.py`'s existing fake payments API (CLAUDE.md rule #3 — no mock data
pretending to be real integrations, say so explicitly), for a reason
specific to Razorpay's own platform, not just "no credentials yet":

Razorpay's Orders API (https://razorpay.com/docs/api/orders/) is a genuine
server-to-server call and *could* be made real with test-mode keys
(`RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`, both unset in this environment —
generating them requires a business PAN this project doesn't have).
Payment *capture*, however, can never be made real from a pure backend
script regardless of credentials: Razorpay's own Payments API docs state
it can only retrieve or capture a payment that already exists, "not to
collect payments" — a payment fundamentally originates through their
hosted Checkout (a browser in the loop) or one of their client SDKs, by
design (PCI-DSS scope), not a BASTION or credentials gap. So this module's
two steps are asymmetric on purpose: `create_order` has a real branch
that activates the moment credentials exist; `capture_payment` is a
permanent, honest simulation with no swap point, because there is no
real backend-only version of it to swap in.

Every value this module returns includes `"simulated": True/False` inline
in the data itself, not just in this docstring — so a viewer reading a
receipt straight out of `GET /traces/{id}` (not this source file) still
sees the disclosure, the same way a trace inspector would.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")
RAZORPAY_API_BASE = "https://api.razorpay.com/v1"

CATALOG_URL = os.environ.get("CATALOG_URL", "http://localhost:4003")


async def fetch_catalog() -> list[dict[str, Any]]:
    """Real HTTP call, no stubbing here — the catalog service is genuinely
    running and this genuinely calls it."""
    async with httpx.AsyncClient(base_url=CATALOG_URL, timeout=10.0) as http:
        response = await http.get("/catalog")
        response.raise_for_status()
        result: list[dict[str, Any]] = response.json()
        return result


def _fake_id(prefix: str) -> str:
    # Razorpay's own ids look like "order_RB58MiP5SPFYyM" / "pay_...";
    # shaped the same way so a receipt reads like a real one at a glance —
    # the `simulated` field is what actually discloses it, not the shape.
    return f"{prefix}_{uuid.uuid4().hex[:14]}"


async def create_order(*, amount_inr: int, receipt: str) -> dict[str, Any]:
    """`POST /v1/orders` — real if RAZORPAY_KEY_ID/SECRET are set, since
    order creation is a genuine, PCI-out-of-scope server-to-server call
    Razorpay's API actually supports. This is the one-line swap point:
    the `if RAZORPAY_KEY_ID...` branch below is untested (no test-mode
    account was available to verify it against), so it's flagged as such
    rather than presented with the same confidence as the rest of this
    codebase's tested code."""
    amount_paise = amount_inr * 100
    if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
        # UNTESTED — no Razorpay test-mode account was available in this
        # environment to verify this branch against the real API. Shaped
        # from Razorpay's documented request/response per
        # https://razorpay.com/docs/api/orders/create/, not guessed, but
        # "read the docs correctly" and "verified against the real
        # endpoint" are not the same claim — don't treat this branch with
        # the same confidence as the rest of this codebase until it's
        # actually been run once against a real test-mode account.
        async with httpx.AsyncClient(
            base_url=RAZORPAY_API_BASE,
            auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            timeout=10.0,
        ) as http:
            response = await http.post(
                "/orders",
                json={"amount": amount_paise, "currency": "INR", "receipt": receipt},
            )
            response.raise_for_status()
            order: dict[str, Any] = response.json()
            order["simulated"] = False
            return order

    return {
        "id": _fake_id("order"),
        "amount": amount_paise,
        "amount_paid": 0,
        "amount_due": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "status": "created",
        "created_at": int(time.time()),
        "simulated": True,
    }


async def capture_payment(*, order: dict[str, Any]) -> dict[str, Any]:
    """Always simulated — see the module docstring for why this one has
    no real branch to swap in at all: Razorpay's Payments API explicitly
    only acts on a payment that already exists (capture/fetch/refund),
    never creates one. A real payment can only originate through
    Razorpay's hosted Checkout or client SDKs, which this backend-only
    agent doesn't have."""
    return {
        "id": _fake_id("pay"),
        "order_id": order["id"],
        "amount": order["amount"],
        "currency": order["currency"],
        "status": "captured",
        "method": "card",
        "created_at": int(time.time()),
        "simulated": True,
    }


async def purchase(*, sku: str, name: str, price_inr: int, quantity: int = 1) -> dict[str, Any]:
    """The full `razorpay.purchase` tool — order, then capture, then a
    receipt shaped the way a real one would be, with `simulated` fields
    carried through from each step rather than collapsed away."""
    amount_inr = price_inr * quantity
    order = await create_order(amount_inr=amount_inr, receipt=f"{sku}-{uuid.uuid4().hex[:8]}")
    payment = await capture_payment(order=order)
    return {
        "order_id": order["id"],
        "payment_id": payment["id"],
        "sku": sku,
        "item_name": name,
        "quantity": quantity,
        "amount_inr": amount_inr,
        "status": payment["status"],
        "simulated": order["simulated"] or payment["simulated"],
    }
