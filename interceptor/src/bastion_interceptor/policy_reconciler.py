"""Periodic policy-cache reconciliation loop — U5 (v2 upgrade),
UPGRADE_ARCHITECTURE.md §6's explicit failure case: what if an interceptor
misses the Redis pub/sub broadcast (down at publish time, message dropped —
redis_bus.py's pub/sub has no delivery guarantee and no replay)? A missed
message leaves that instance permanently serving a stale policy version
with nothing to ever correct it, unless something else periodically
re-checks. This loop is that something: every `interval_seconds`, compare
every active policy in Postgres (the source of truth, ADR-001) against
what this instance's PolicyCache actually holds, and self-heal any
mismatch — turning "eventually consistent, hopefully" into a bounded,
provable convergence guarantee instead of an assumption.
"""

from __future__ import annotations

import asyncio
import contextlib

import structlog

from . import policy as policy_engine
from .db import db

log = structlog.get_logger()


async def reconcile_once() -> int:
    """One reconciliation sweep. Returns how many cache entries were
    corrected (put or evicted) — 0 means the cache was already fully
    converged with Postgres. Reuses exactly the same compile+put logic
    main.py's `_reload_policy_set` (the Redis listener's callback) uses, so
    a self-heal here is indistinguishable from a normal hot-reload once
    applied — this is a second path to the same converged state, not a
    parallel one with its own rules."""
    active_records = await db.get_active_policies()
    active_by_set = {record["policy_set_id"]: record for record in active_records}

    healed = 0
    for policy_set_id, record in active_by_set.items():
        cached = policy_engine.policy_cache.get(policy_set_id)
        if cached is not None and cached.policy_id == record["id"]:
            continue  # already converged
        compiled = policy_engine.compile_policy_from_raw(
            record["id"], record["policy_set_id"], record["definition"]
        )
        policy_engine.policy_cache.put(compiled)
        log.warning(
            "policy cache reconciliation healed a drifted entry",
            policy_set_id=str(policy_set_id),
            policy_id=str(record["id"]),
        )
        healed += 1

    # The other direction of drift: a set this instance still has cached as
    # active, but Postgres no longer considers active at all (e.g. every
    # version was deactivated, or activate_policy ran for it and this
    # instance missed *that* broadcast too) — a plain per-active-set lookup
    # above can never find this case, since it only ever iterates sets
    # Postgres currently calls active.
    for policy_set_id in policy_engine.policy_cache.cached_set_ids() - active_by_set.keys():
        policy_engine.policy_cache.evict(policy_set_id)
        log.warning(
            "policy cache reconciliation evicted a stale entry",
            policy_set_id=str(policy_set_id),
        )
        healed += 1

    return healed


class PolicyReconciler:
    def __init__(self, *, interval_seconds: float) -> None:
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run_forever(self) -> None:
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                await reconcile_once()
            except Exception:
                # A failed sweep (e.g. Postgres transiently unreachable)
                # just means this instance stays on whatever it had —
                # correct-if-stale, never wrong — and gets another chance
                # next interval. Never let one bad sweep kill the loop.
                log.exception("policy cache reconciliation sweep failed")
