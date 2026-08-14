"""Human-authorization decisions — U7 (v2 upgrade), UPGRADE_ARCHITECTURE.md
§9: an explicit `Subject -> Role -> Resource -> Action -> Policy` evaluation
chain, answering questions like "can approver X approve a $250 payment for
agent Y" as a single traceable evaluation — the same shape as the tool-call
policy engine's own evaluation, reusing its mechanics rather than building a
second implementation (§9: "one evaluator, two rule sets").

The reuse is literal, not just conceptual: an authorization policy is a
normal `policies`/`policy_sets` row (same table, same versioning, same
POST /policies + POST /policies/{id}/activate endpoints, same in-memory
PolicyCache) — it's simply looked up by a reserved, well-known name per org
instead of by `agents.default_policy_set_id`. `policy_engine.evaluate()` is
called with `tool_name=action` and `args=resource` — everything downstream
(match/condition/limits, the safe-eval walker, the compiled-rule cache) is
the exact same code a tool-call decision goes through, unmodified. No new
table, no new endpoint, no second evaluator.

Subject/Role: the caller's `AuthenticatedUser` (`user.id`/`user.role`).
Resource/Action: whatever the call site builds — main.py's approve/deny
flow passes `action="approve"`/`"deny"` and a `resource` dict built from
the underlying call's own CallAttempted payload (`tool_name`, `args`),
plus the subject's own role folded in as `resource["role"]` so a rule can
condition on who's asking, not just what's being approved.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from . import policy as policy_engine
from .db import db

# Reserved policy_set name, per org — deliberately not user-facing/
# configurable as a separate concept: an org's authorization policy is
# created and activated through the exact same POST /policies +
# POST /policies/{id}/activate endpoints as any tool-call policy, just
# under this one well-known name.
AUTHORIZATION_POLICY_SET_NAME = "__bastion_authorization__"


async def check_authorization(
    *, org_id: UUID, action: str, resource: dict[str, Any]
) -> policy_engine.Decision:
    """No authorization policy configured for this org: safe default,
    `allow` — matches `evaluate(None, ...)`'s existing behavior for
    tool-call policies, and preserves exact backward compatibility for
    every org that predates this feature (RBAC role checks, e.g.
    `require_approver`, still apply independently either way — this is an
    *additional* restriction layer, never a replacement for them)."""
    policy_set_id = await db.get_policy_set_id_by_name(org_id, AUTHORIZATION_POLICY_SET_NAME)
    if policy_set_id is None:
        return policy_engine.Decision(action="allow")
    compiled = policy_engine.policy_cache.get(policy_set_id)
    return policy_engine.evaluate(compiled, action, resource)
