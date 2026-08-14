"""Multi-dimensional rate limiting / cost governance — U6 (v2 upgrade),
UPGRADE_ARCHITECTURE.md §8. Real Redis-backed enforcement for a matched
rule's `limits:` block, checked once policy evaluation (policy.py's
`evaluate`) has already decided a call would otherwise be `allow` — see
ADR-015 for why this lives as a separate stateful step rather than folding
into the `condition` field's safe-eval walker (which is deliberately
stateless and args-only, with no access to Redis or cross-call counters).

Scope, stated explicitly rather than silently narrowed (ADR-015):
implements `max_transaction_amount` (pure comparison, no state),
`calls_per_minute` (a fixed-window Redis counter that does double duty for
both "per agent" and "per tool" from §8's list — a rule matching
`tool: "*"` scopes it per-agent, a rule naming a specific tool scopes it
per-tool, same mechanism either way), and `org_spend_per_day` /
`agent_llm_budget_per_hour` (a shared check-then-commit spend accumulator,
differing only in window/key). A distinct tool-call-count budget and a
runtime/duration budget are NOT implemented — the former is redundant with
`calls_per_minute`, the latter isn't knowable until CallCompleted/
CallFailed, well after the decision point these limits gate.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import redis.asyncio as redis
from bastion_shared import PolicyLimits


async def check_and_apply_limits(
    *,
    redis_client: redis.Redis,
    agent_id: UUID,
    org_id: UUID,
    rule_tool: str,
    args: dict[str, Any],
    limits: PolicyLimits,
) -> str | None:
    """Returns a block reason if any configured limit is exceeded, else
    None. Checked in this order — transaction cap, then call volume, then
    spend — so a single call is only ever charged against the limits it
    actually passed: a call that violates a hard per-transaction cap
    shouldn't also consume rate/spend budget on its way to being rejected.
    """
    amount = args.get("amount")
    amount_value = float(amount) if isinstance(amount, (int, float)) else 0.0

    if limits.max_transaction_amount is not None and amount_value > limits.max_transaction_amount:
        return (
            f"transaction amount {amount_value} exceeds max_transaction_amount "
            f"{limits.max_transaction_amount}"
        )

    if limits.calls_per_minute is not None:
        key = f"bastion:limits:calls:{agent_id}:{rule_tool}"
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, 60)
        if count > limits.calls_per_minute:
            return (
                f"calls_per_minute limit {limits.calls_per_minute} exceeded for tool '{rule_tool}'"
            )

    if limits.org_spend_per_day is not None:
        reason = await _check_and_commit_spend(
            redis_client,
            key=f"bastion:limits:spend:org:{org_id}",
            window_seconds=86400,
            cap=limits.org_spend_per_day,
            amount=amount_value,
            label="org_spend_per_day",
        )
        if reason is not None:
            return reason

    if limits.agent_llm_budget_per_hour is not None:
        reason = await _check_and_commit_spend(
            redis_client,
            key=f"bastion:limits:spend:agent_llm:{agent_id}",
            window_seconds=3600,
            cap=limits.agent_llm_budget_per_hour,
            amount=amount_value,
            label="agent_llm_budget_per_hour",
        )
        if reason is not None:
            return reason

    return None


async def _check_and_commit_spend(
    redis_client: redis.Redis,
    *,
    key: str,
    window_seconds: int,
    cap: float,
    amount: float,
    label: str,
) -> str | None:
    """Check-then-commit, not a single atomic Redis operation — a known,
    documented simplification (ADR-015). A production-hardened version
    would close the TOCTOU window between the GET and the INCRBYFLOAT below
    with a Lua script; acceptable as-is at this system's current scale and
    out of scope to harden further this phase."""
    current_raw = await redis_client.get(key)
    current = float(current_raw) if current_raw is not None else 0.0
    if current + amount > cap:
        return f"{label} cap {cap} would be exceeded ({current} + {amount} > {cap})"
    await redis_client.incrbyfloat(key, amount)
    await redis_client.expire(key, window_seconds, nx=True)
    return None
