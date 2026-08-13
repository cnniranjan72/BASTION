from __future__ import annotations

from uuid import UUID


class BastionBlockedError(Exception):
    """Raised when policy blocks a call — the wrapped `execute` callback is
    never invoked, which is the actual mechanism that prevents the dangerous
    action from happening (PRD.md §5.1), not just advises against it."""

    def __init__(self, reason: str, span_id: UUID, policy_id: UUID | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.span_id = span_id
        self.policy_id = policy_id


class BastionPendingApprovalError(Exception):
    """Raised for a pending_approval decision. The long-poll/resume flow
    lands in Phase 3; for now this just surfaces that the call can't proceed
    synchronously."""

    def __init__(self, span_id: UUID, approval_request_id: UUID) -> None:
        super().__init__(f"call {span_id} is pending approval ({approval_request_id})")
        self.span_id = span_id
        self.approval_request_id = approval_request_id
