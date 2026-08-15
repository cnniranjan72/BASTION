import { beforeEach, describe, expect, it } from "vitest";
import type { LiveMessage, TraceGraph } from "../api/types";
import { useGraphStore } from "./graph";

beforeEach(() => {
  useGraphStore.getState().reset();
});

describe("useGraphStore.applyLiveMessage", () => {
  it("node_added creates a pending node with no parent yet", () => {
    const msg: LiveMessage = {
      type: "node_added",
      trace_id: "trace-1",
      node: { span_id: "span-1", tool_name: "payments.transfer", status: "pending" },
    };
    useGraphStore.getState().applyLiveMessage(msg);

    const node = useGraphStore.getState().nodes.get("span-1");
    expect(node).toMatchObject({
      span_id: "span-1",
      tool_name: "payments.transfer",
      status: "pending",
      parent_span_id: null,
    });
  });

  it("node_updated preserves the reason field — the exact bug ARCHITECTURE.md §17 documents finding live", () => {
    useGraphStore.getState().applyLiveMessage({
      type: "node_added",
      trace_id: "trace-1",
      node: { span_id: "span-1", tool_name: "payments.transfer", status: "pending" },
    });
    useGraphStore.getState().applyLiveMessage({
      type: "node_updated",
      trace_id: "trace-1",
      span_id: "span-1",
      status: "blocked",
      reason: "amount > 100",
    });

    const node = useGraphStore.getState().nodes.get("span-1");
    expect(node?.status).toBe("blocked");
    expect(node?.reason).toBe("amount > 100");
  });

  it("node_updated on an unknown span is a no-op, not a crash", () => {
    useGraphStore.getState().applyLiveMessage({
      type: "node_updated",
      trace_id: "trace-1",
      span_id: "never-added",
      status: "allowed",
    });
    expect(useGraphStore.getState().nodes.size).toBe(0);
  });

  it("node_updated without a new reason keeps the existing one, not clobbering it with undefined", () => {
    useGraphStore.getState().applyLiveMessage({
      type: "node_added",
      trace_id: "trace-1",
      node: { span_id: "span-1", tool_name: "x", status: "pending" },
    });
    useGraphStore.getState().applyLiveMessage({
      type: "node_updated",
      trace_id: "trace-1",
      span_id: "span-1",
      status: "blocked",
      reason: "first reason",
    });
    useGraphStore.getState().applyLiveMessage({
      type: "node_updated",
      trace_id: "trace-1",
      span_id: "span-1",
      status: "completed",
    });

    expect(useGraphStore.getState().nodes.get("span-1")?.reason).toBe("first reason");
  });

  it("edge_added links parent_span_id on the target node and records the edge", () => {
    useGraphStore.getState().applyLiveMessage({
      type: "node_added",
      trace_id: "trace-1",
      node: { span_id: "child", tool_name: "x", status: "pending" },
    });
    useGraphStore.getState().applyLiveMessage({ type: "edge_added", from: "root", to: "child" });

    expect(useGraphStore.getState().edges).toEqual([{ from: "root", to: "child" }]);
    expect(useGraphStore.getState().nodes.get("child")?.parent_span_id).toBe("root");
  });

  it("edge_added targeting a not-yet-added node still records the edge, without crashing", () => {
    useGraphStore.getState().applyLiveMessage({ type: "edge_added", from: "root", to: "child" });
    expect(useGraphStore.getState().edges).toEqual([{ from: "root", to: "child" }]);
    expect(useGraphStore.getState().nodes.has("child")).toBe(false);
  });

  it("edge_added does not append a timeline row (structural only)", () => {
    useGraphStore.getState().applyLiveMessage({ type: "edge_added", from: "root", to: "child" });
    expect(useGraphStore.getState().timeline).toHaveLength(0);
  });

  it("node_added and node_updated each append exactly one timeline row", () => {
    useGraphStore.getState().applyLiveMessage({
      type: "node_added",
      trace_id: "trace-1",
      node: { span_id: "span-1", tool_name: "x", status: "pending" },
    });
    useGraphStore.getState().applyLiveMessage({
      type: "node_updated",
      trace_id: "trace-1",
      span_id: "span-1",
      status: "allowed",
    });
    expect(useGraphStore.getState().timeline).toHaveLength(2);
    expect(useGraphStore.getState().timeline.map((t) => t.type)).toEqual([
      "node_added",
      "node_updated",
    ]);
  });
});

describe("useGraphStore.loadSnapshot", () => {
  const snapshot: TraceGraph = {
    trace_id: "trace-1",
    agent_id: "agent-1",
    status: "had_blocks",
    total_cost: 0,
    total_calls: 1,
    blocked_calls: 1,
    started_at: "2026-01-01T00:00:00.000Z",
    ended_at: "2026-01-01T00:00:01.000Z",
    nodes: [
      {
        span_id: "span-1",
        parent_span_id: null,
        tool_name: "payments.transfer",
        status: "blocked",
        args: null,
        latency_ms: null,
        cost: null,
        reason: "amount > 100",
        trace_id: null,
        updated_at: null,
      },
    ],
    edges: [],
  };

  it("replaces all state wholesale and stamps trace_id onto each node", () => {
    useGraphStore.getState().applyLiveMessage({
      type: "node_added",
      trace_id: "stale-trace",
      node: { span_id: "stale-span", tool_name: "x", status: "pending" },
    });

    useGraphStore.getState().loadSnapshot(snapshot);

    const state = useGraphStore.getState();
    expect(state.nodes.has("stale-span")).toBe(false);
    expect(state.nodes.get("span-1")?.trace_id).toBe("trace-1");
    expect(state.traceStatus).toBe("had_blocks");
    expect(state.timeline).toHaveLength(0);
    expect(state.selectedSpanId).toBeNull();
  });
});

describe("useGraphStore.selectNode / reset", () => {
  it("selectNode sets and clears selection", () => {
    useGraphStore.getState().selectNode("span-1");
    expect(useGraphStore.getState().selectedSpanId).toBe("span-1");
    useGraphStore.getState().selectNode(null);
    expect(useGraphStore.getState().selectedSpanId).toBeNull();
  });

  it("reset clears nodes, edges, timeline, and selection", () => {
    useGraphStore.getState().applyLiveMessage({
      type: "node_added",
      trace_id: "trace-1",
      node: { span_id: "span-1", tool_name: "x", status: "pending" },
    });
    useGraphStore.getState().selectNode("span-1");

    useGraphStore.getState().reset();

    const state = useGraphStore.getState();
    expect(state.nodes.size).toBe(0);
    expect(state.edges).toHaveLength(0);
    expect(state.timeline).toHaveLength(0);
    expect(state.selectedSpanId).toBeNull();
    expect(state.traceStatus).toBeNull();
  });
});
