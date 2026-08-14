import type { NodeStatus, TraceStatus } from "../api/types";

// Plain-English labels for the raw enum values the backend uses — those
// enums are good identifiers for code, bad copy for a screen a non-engineer
// has to read. Every place that renders a status should go through these
// instead of printing the raw string.

export const TRACE_STATUS_LABEL: Record<TraceStatus, string> = {
  running: "Running",
  completed: "Completed",
  had_blocks: "Had blocked calls",
  failed: "Failed",
};

export const TRACE_STATUS_DESCRIPTION: Record<TraceStatus, string> = {
  running: "Still in progress — the agent hasn't finished this task yet.",
  completed: "Finished, and every call in it was allowed.",
  had_blocks: "Finished, but a policy stopped at least one call in it.",
  failed: "The trace itself errored — not a policy block, something broke.",
};

export const NODE_STATUS_LABEL: Record<NodeStatus, string> = {
  pending: "Deciding…",
  allowed: "Allowed — running",
  pending_approval: "Waiting for a human",
  completed: "Completed",
  blocked: "Blocked by policy",
  failed: "Errored",
};

export const NODE_STATUS_DESCRIPTION: Record<NodeStatus, string> = {
  pending: "BASTION hasn't decided whether to allow this call yet.",
  allowed: "The policy allowed this call and it's currently running.",
  pending_approval: "A policy sent this to a human — it's paused until someone approves or denies it.",
  completed: "This call ran and finished successfully.",
  blocked: "A policy stopped this call before it could run.",
  failed: "This call ran but the underlying action errored.",
};
