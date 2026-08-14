import { create } from "zustand";
import type { GraphNode, LiveMessage, NodeStatus, TraceGraph } from "../api/types";

interface GraphState {
  nodes: Map<string, GraphNode>;
  edges: Array<{ from: string; to: string }>;
  selectedSpanId: string | null;
  traceStatus: "running" | "completed" | "failed" | "had_blocks" | null;

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

  loadSnapshot: (graph) => {
    const nodes = new Map<string, GraphNode>();
    for (const node of graph.nodes) nodes.set(node.span_id, node);
    set({
      nodes,
      edges: graph.edges.map((e) => ({ from: e.from, to: e.to })),
      selectedSpanId: null,
      traceStatus: graph.status,
    });
  },

  applyLiveMessage: (message) => {
    const nodes = new Map(get().nodes);
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
        });
        set({ nodes });
        break;
      case "node_updated": {
        const existing = nodes.get(message.span_id);
        if (!existing) return;
        nodes.set(message.span_id, {
          ...existing,
          status: message.status as NodeStatus,
          latency_ms: message.latency_ms ?? existing.latency_ms,
          cost: message.cost ?? existing.cost,
        });
        set({ nodes });
        break;
      }
      case "edge_added": {
        const target = nodes.get(message.to);
        if (target) nodes.set(message.to, { ...target, parent_span_id: message.from });
        set({
          nodes,
          edges: [...get().edges, { from: message.from, to: message.to }],
        });
        break;
      }
    }
  },

  selectNode: (spanId) => set({ selectedSpanId: spanId }),

  reset: () => set({ nodes: new Map(), edges: [], selectedSpanId: null, traceStatus: null }),
}));
