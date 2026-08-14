import type { NodeStatus } from "../../api/types";

// ARCHITECTURE.md §2.6: "node color = status ... node size = cost/latency."
// pending/allowed are transient (a call is still in flight); completed is
// the good resting state; blocked/failed are both "went wrong" but kept
// visually distinct (blocked = policy decision, failed = the call itself
// errored) since they mean different things to someone debugging a trace.
const STATUS_COLOR: Record<NodeStatus, string> = {
  pending: "#94a3b8", // slate — not yet decided
  allowed: "#38bdf8", // sky — permitted, still running
  pending_approval: "#f59e0b", // amber — waiting on a human
  completed: "#22c55e", // emerald — finished cleanly
  blocked: "#ef4444", // red — policy stopped it
  failed: "#b91c1c", // darker red — it ran and errored
};

export function colorForStatus(status: NodeStatus): string {
  return STATUS_COLOR[status];
}

const MIN_RADIUS = 0.28;
const MAX_RADIUS = 0.85;
const LATENCY_CAP_MS = 3000;
const COST_CAP = 1;

export function radiusForNode(latencyMs: number | null, cost: number | null): number {
  const latencyFraction = Math.min(latencyMs ?? 0, LATENCY_CAP_MS) / LATENCY_CAP_MS;
  const costFraction = Math.min(cost ?? 0, COST_CAP) / COST_CAP;
  // Whichever signal is available (most calls have latency; only some have
  // a $ cost) dominates rather than averaging them down.
  const fraction = Math.max(latencyFraction, costFraction);
  return MIN_RADIUS + fraction * (MAX_RADIUS - MIN_RADIUS);
}

export function isTransient(status: NodeStatus): boolean {
  return status === "pending" || status === "allowed";
}
