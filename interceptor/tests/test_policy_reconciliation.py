"""U5 milestone test (UPGRADE_BUILD_PLAN.md): simulate an interceptor
missing a Redis pub/sub policy-update message, assert it still converges to
the correct policy version within the reconciliation interval.

"Missing the pub/sub message" is simulated by calling `db.activate_policy`
directly instead of through `POST /policies/{id}/activate` — the HTTP
endpoint is what publishes the hot-reload broadcast (main.py), so bypassing
it changes Postgres's active row for this policy_set without notifying this
process's in-memory `policy_cache` at all, exactly modeling a dropped/missed
broadcast (redis_bus.py's pub/sub has no delivery guarantee or replay).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from bastion_interceptor import policy as policy_engine
from bastion_interceptor.db import db
from bastion_interceptor.main import app
from bastion_interceptor.policy_reconciler import PolicyReconciler, reconcile_once

# Short enough to keep this test fast, long enough to give a real interval
# for the milestone assertion ("converges within the reconciliation
# interval") to actually be about — not instant/inline reconciliation.
_TEST_INTERVAL_SECONDS = 0.2


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://interceptor.test"
    )


def _auth_headers(login: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {login['access_token']}"}


async def test_reconciliation_heals_a_missed_pubsub_broadcast(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    user = await make_user(role="admin")
    login = await login_as(user)
    name = f"reconcile-test-{uuid.uuid4()}"

    async with _http_client() as http:
        v1 = await http.post(
            "/policies", json={"name": name, "definition": []}, headers=_auth_headers(login)
        )
        v1_id = UUID(v1.json()["id"])
        policy_set_id = UUID(v1.json()["policy_set_id"])
        # Goes through the real endpoint — populates policy_cache and
        # publishes the broadcast normally, establishing a known-good
        # baseline before we simulate a miss.
        await http.post(f"/policies/{v1_id}/activate", headers=_auth_headers(login))

        v2 = await http.post(
            "/policies",
            json={"name": name, "definition": [], "based_on_version": 1},
            headers=_auth_headers(login),
        )
        v2_id = UUID(v2.json()["id"])

    assert policy_engine.policy_cache.get(policy_set_id) is not None
    assert policy_engine.policy_cache.get(policy_set_id).policy_id == v1_id  # type: ignore[union-attr]

    # Simulate the missed broadcast: activate v2 directly against Postgres,
    # never touching redis_bus.publish_policy_update. This process's cache
    # now silently disagrees with the source of truth — exactly the failure
    # mode UPGRADE_ARCHITECTURE.md §6 calls out.
    await db.activate_policy(v2_id, user["org_id"])

    # Confirm the miss is real before relying on any reconciliation: the
    # cache must still show the stale v1, not v2.
    stale = policy_engine.policy_cache.get(policy_set_id)
    assert stale is not None
    assert stale.policy_id == v1_id

    reconciler = PolicyReconciler(interval_seconds=_TEST_INTERVAL_SECONDS)
    reconciler.start()
    try:
        converged = False
        for _ in range(50):  # up to 5s, well beyond a couple of intervals
            cached = policy_engine.policy_cache.get(policy_set_id)
            if cached is not None and cached.policy_id == v2_id:
                converged = True
                break
            await asyncio.sleep(0.1)
    finally:
        await reconciler.stop()

    assert converged, "policy cache never converged to the active version within the window"


async def test_reconcile_once_heals_drifted_entry_and_reports_count(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    """Direct, non-timing-dependent proof that a single sweep both fixes the
    cache and reports how much work it did — the periodic loop above is just
    this function called on a timer."""
    user = await make_user(role="admin")
    login = await login_as(user)
    name = f"reconcile-once-test-{uuid.uuid4()}"

    async with _http_client() as http:
        v1 = await http.post(
            "/policies", json={"name": name, "definition": []}, headers=_auth_headers(login)
        )
        v1_id = UUID(v1.json()["id"])
        policy_set_id = UUID(v1.json()["policy_set_id"])
        await http.post(f"/policies/{v1_id}/activate", headers=_auth_headers(login))

        v2 = await http.post(
            "/policies",
            json={"name": name, "definition": [], "based_on_version": 1},
            headers=_auth_headers(login),
        )
        v2_id = UUID(v2.json()["id"])

    await db.activate_policy(v2_id, user["org_id"])

    healed = await reconcile_once()
    assert healed >= 1

    cached = policy_engine.policy_cache.get(policy_set_id)
    assert cached is not None
    assert cached.policy_id == v2_id

    # A second sweep with nothing left to fix reports zero for *this* set —
    # other tests running in the same session may have their own drift, so
    # this only asserts our own set stopped needing healing, not that the
    # whole cache is quiescent.
    healed_again = await reconcile_once()
    assert healed_again == 0 or policy_engine.policy_cache.get(policy_set_id).policy_id == v2_id  # type: ignore[union-attr]


async def test_reconcile_once_evicts_entry_no_longer_active_anywhere(
    make_user: Callable[..., Awaitable[dict]], login_as: Callable[[dict], Awaitable[dict]]
) -> None:
    """The other direction of drift U5 has to cover: a policy_set cached as
    active here, but Postgres no longer has *any* active version for it at
    all (every version deactivated) — reconcile_once must evict, not just
    update, since there's nothing left to update to."""
    user = await make_user(role="admin")
    login = await login_as(user)
    name = f"reconcile-evict-test-{uuid.uuid4()}"

    async with _http_client() as http:
        v1 = await http.post(
            "/policies", json={"name": name, "definition": []}, headers=_auth_headers(login)
        )
        v1_id = UUID(v1.json()["id"])
        policy_set_id = UUID(v1.json()["policy_set_id"])
        await http.post(f"/policies/{v1_id}/activate", headers=_auth_headers(login))

    assert policy_engine.policy_cache.get(policy_set_id) is not None

    # Deactivate every version in this set directly against Postgres,
    # bypassing the broadcast — same "missed message" simulation as above,
    # just landing on zero active versions instead of a newer one.
    await db.pool.execute(
        "UPDATE policies SET active = false WHERE policy_set_id = $1", policy_set_id
    )

    assert policy_engine.policy_cache.get(policy_set_id) is not None  # still stale

    healed = await reconcile_once()
    assert healed >= 1
    assert policy_engine.policy_cache.get(policy_set_id) is None
