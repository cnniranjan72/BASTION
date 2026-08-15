import { create } from "zustand";
import type { GraphNode, LiveMessage, NodeStatus, TraceGraph } from "../api/types";

// U15 (v2 upgrade): the Live Execution Graph's timeline strip
// (FRONTEND_V2.md's "a horizontal event timeline below the graph").
// timestamp is client-receive time, not the server's real event
// created_at — LiveMessage carries no timestamp of its own (deliberate
// scope boundary, see docs/PROGRESS.md's U15 entry). This is fine for a
// *live* view (sub-second lag between the real event and this message
// arriving), unlike Incident Replay, which uses the real historical
// event log timestamps instead — a different store entirely
// (store/replay.ts), not this one.
export interface TimelineEntry {
  id: string;
  trace_id: string;
  span_id: string;
  type: "node_added" | "node_updated" | "edge_added";
  tool_name: string | null;
  status: NodeStatus | null;
  reason: string | null;
  timestamp: number;
}

interface GraphState {
  nodes: Map<string, GraphNode>;
  edges: Array<{ from: string; to: string }>;
  selectedSpanId: string | null;
  traceStatus: "running" | "completed" | "failed" | "had_blocks" | null;
  timeline: TimelineEntry[];

  loadSnapshot: (graph: TraceGraph) => void;
  applyLiveMessage: (message: LiveMessage) => void;
  selectNode: (spanId: string | null) => void;
  reset: () => void;
}

// Delta application only — never rebuilds nodes/edges wholesale on a live
// update, matching ARCHITECTURE.md §2.6 ("does NOT re-render the whole
// graph on every event"). loadSnapshot is the one place that replaces
// everything at once, used only when switching to a different trace.
export const useGraphStore = create<GraphState>((set, get) => ({
  nodes: new Map(),
  edges: [],
  selectedSpanId: null,
  traceStatus: null,
  timeline: [],

  loadSnapshot: (graph) => {
    const nodes = new Map<string, GraphNode>();
    for (const node of graph.nodes) {
      nodes.set(node.span_id, {
        ...node,
        trace_id: graph.trace_id,
        updated_at: Date.parse(graph.ended_at ?? graph.started_at),
      });
    }
    set({
      nodes,
      edges: graph.edges.map((e) => ({ from: e.from, to: e.to })),
      selectedSpanId: null,
      traceStatus: graph.status,
      timeline: [],
    });
  },

  applyLiveMessage: (message) => {
    const nodes = new Map(get().nodes);
    let timelineEntry: TimelineEntry | null = null;
    switch (message.type) {
      case "node_added":
        nodes.set(message.node.span_id, {
          span_id: message.node.span_id,
          parent_span_id: null, // filled in by the accompanying edge_added, if any
          tool_name: message.node.tool_name,
          status: message.node.status,
          args: null,
          latency_ms: null,
          cost: null,
          reason: null,
          trace_id: message.trace_id,
          updated_at: Date.now(),
        });
        set({ nodes });
        timelineEntry = {
          id: crypto.randomUUID(),
          trace_id: message.trace_id,
          span_id: message.node.span_id,
          type: "node_added",
          tool_name: message.node.tool_name,
          status: "pending",
          reason: null,
          timestamp: Date.now(),
        };
        break;
      case "node_updated": {
        const existing = nodes.get(message.span_id);
        if (!existing) return;
        nodes.set(message.span_id, {
          ...existing,
          status: message.status as NodeStatus,
          latency_ms: message.latency_ms ?? existing.latency_ms,
          cost: message.cost ?? existing.cost,
          reason: message.reason ?? existing.reason,
          updated_at: Date.now(),
        });
        set({ nodes });
        timelineEntry = {
          id: crypto.randomUUID(),
          trace_id: message.trace_id,
          span_id: message.span_id,
          type: "node_updated",
          tool_name: existing.tool_name,
          status: message.status,
          reason: message.reason ?? null,
          timestamp: Date.now(),
        };
        break;
      }
      case "edge_added": {
        const target = nodes.get(message.to);
        if (target) nodes.set(message.to, { ...target, parent_span_id: message.from });
        set({
          nodes,
          edges: [...get().edges, { from: message.from, to: message.to }],
        });
        // Structural, not a decision — not worth its own timeline row;
        // the node_added row already anchors this span in the timeline.
        return;
      }
    }
    if (timelineEntry) set({ timeline: [...get().timeline, timelineEntry] });
  },

  selectNode: (spanId) => set({ selectedSpanId: spanId }),

  reset: () =>
    set({ nodes: new Map(), edges: [], selectedSpanId: null, traceStatus: null, timeline: [] }),
}));
