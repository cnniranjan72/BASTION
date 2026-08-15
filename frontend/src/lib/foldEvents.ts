import type { GraphEdge, GraphNode, RawEvent, TraceGraph, TraceStatus } from "../api/types";

// U15 (v2 upgrade), Incident Replay. Direct TypeScript port of
// aggregator/src/bastion_aggregator/graph.py's fold_events_to_graph — the
// same fold, client-side, so the replay engine can re-derive graph state
// at any point along a prefix of the real event log rather than needing a
// backend endpoint per scrubbed position. FRONTEND_V2.md is explicit that
// replay "is reconstructed purely from the immutable events table — no
// separate 'replay data' storage"; this keeps that true on the frontend
// too (no synthetic replay-only backend shape), at the cost of having to
// keep this in sync with graph.py by hand if that logic ever changes —
// an accepted drift risk, same class already accepted for api/types.ts
// (see that file's own top-of-file note).
const CREATION_EVENT = "CallAttempted";

const STATUS_FOR_EVENT_TYPE: Record<string, GraphNode["status"]> = {
  CallAllowed: "allowed",
  ApprovalGranted: "allowed",
  CallBlocked: "blocked",
  ApprovalDenied: "blocked",
  CallPendingApproval: "pending_approval",
  CallCompleted: "completed",
  CallFailed: "failed",
};

/** events must be ordered by sequence_number ascending (GET
 * /traces/{id}/events's own guarantee) — a prefix of this list is exactly
 * "the trace as of this point in replay." Throws on an empty list, same
 * as the Python original, since a trace only exists because at least one
 * event was emitted. */
export function foldEventsToGraph(events: RawEvent[]): TraceGraph {
  if (events.length === 0) throw new Error("cannot fold an empty event list into a graph");

  const nodes = new Map<string, GraphNode>();
  const edges: GraphEdge[] = [];
  const traceId = events[0]!.trace_id;
  const agentId = events[0]!.agent_id;
  const startedAt = events[0]!.created_at;
  let rootSpanId: string | null = null;
  let endedAt: string | null = null;

  for (const event of events) {
    const { span_id: spanId, event_type: eventType, payload } = event;

    if (eventType === CREATION_EVENT) {
      nodes.set(spanId, {
        span_id: spanId,
        parent_span_id: event.parent_span_id,
        tool_name: (payload.tool_name as string | undefined) ?? "",
        status: "pending",
        args: (payload.args as Record<string, unknown> | undefined) ?? null,
        latency_ms: null,
        cost: null,
        reason: null,
        trace_id: traceId,
        updated_at: Date.parse(event.created_at),
      });
      if (event.parent_span_id != null) {
        edges.push({ from: event.parent_span_id, to: spanId });
      } else {
        rootSpanId = spanId;
      }
      continue;
    }

    const node = nodes.get(spanId);
    if (!node) continue; // defensive, mirrors graph.py — shouldn't happen for real-order input

    const newStatus = STATUS_FOR_EVENT_TYPE[eventType];
    if (newStatus) node.status = newStatus;
    if (eventType === "CallBlocked" || eventType === "ApprovalDenied") {
      node.reason = (payload.reason as string | undefined) ?? null;
    }
    if (eventType === "CallCompleted" || eventType === "CallFailed") {
      node.latency_ms = (payload.latency_ms as number | undefined) ?? null;
      node.cost = (payload.cost as number | undefined) ?? null;
      if (eventType === "CallFailed") node.reason = (payload.error as string | undefined) ?? null;
    }
    node.updated_at = Date.parse(event.created_at);

    if (
      spanId === rootSpanId &&
      ["CallCompleted", "CallFailed", "CallBlocked", "ApprovalDenied"].includes(eventType)
    ) {
      endedAt = event.created_at;
    }
  }

  const nodeList = [...nodes.values()];
  const totalCost = nodeList.reduce((sum, n) => sum + (n.cost ?? 0), 0);
  const blockedCalls = nodeList.filter((n) => n.status === "blocked").length;
  const failed = nodeList.some((n) => n.status === "failed");

  let status: TraceStatus;
  if (endedAt == null) status = "running";
  else if (failed) status = "failed";
  else if (blockedCalls > 0) status = "had_blocks";
  else status = "completed";

  return {
    trace_id: traceId,
    agent_id: agentId,
    status,
    total_cost: totalCost,
    total_calls: nodeList.length,
    blocked_calls: blockedCalls,
    started_at: startedAt,
    ended_at: endedAt,
    nodes: nodeList,
    edges,
  };
}
