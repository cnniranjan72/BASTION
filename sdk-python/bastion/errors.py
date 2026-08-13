from __future__ import annotations

from uuid import UUID


class BastionBlockedError(Exception):
    """Raised when policy blocks a call — the wrapped `execute` callback is
    never invoked, which is the actual mechanism that prevents the dangerous
    action from happening (PRD.md §5.1), not just advises against it.

    Also raised for a resolved-negative approval outcome (denied, timed out,
    or this client's own wait budget exceeded) — from the caller's
    perspective those are all "the action did not happen," same as a
    straight policy block."""

    def __init__(self, reason: str, span_id: UUID, policy_id: UUID | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.span_id = span_id
        self.policy_id = policy_id
