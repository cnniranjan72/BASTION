import { describe, expect, it } from "vitest";
import type { RawEvent } from "../api/types";
import { foldEventsToGraph } from "./foldEvents";

// This is a direct TypeScript port of aggregator/src/bastion_aggregator/
// graph.py's fold_events_to_graph (see this file's own top-of-file
// comment) — these tests mirror the shape of that module's own Python
// tests, not duplicate-for-duplicate's-sake: a drift between the two
// folds is exactly the kind of bug this file's comment already flags as
// an accepted risk, so this suite is what would actually catch one.

let seq = 0;
function event(overrides: Partial<RawEvent> & Pick<RawEvent, "span_id" | "event_type">): RawEvent {
  seq += 1;
  return {
    event_id: `evt-${seq}`,
    trace_id: "trace-1",
    parent_span_id: null,
    agent_id: "agent-1",
    payload: {},
    sequence_number: seq,
    created_at: `2026-01-01T00:00:0${seq}.000Z`,
    ...overrides,
  };
}

describe("foldEventsToGraph", () => {
  it("throws on an empty event list, matching the Python original", () => {
    expect(() => foldEventsToGraph([])).toThrow(/empty event list/);
  });

  it("a single CallAttempted produces one pending node and a running trace", () => {
    const graph = foldEventsToGraph([
      event({
        span_id: "span-1",
        event_type: "CallAttempted",
        payload: { tool_name: "payments.transfer", args: { amount: 25 } },
      }),
    ]);
    expect(graph.status).toBe("running");
    expect(graph.ended_at).toBeNull();
    expect(graph.nodes).toHaveLength(1);
    expect(graph.nodes[0]).toMatchObject({
      span_id: "span-1",
      status: "pending",
      tool_name: "payments.transfer",
      args: { amount: 25 },
    });
  });

  it("CallBlocked sets status and reason, and ends the trace as had_blocks", () => {
    const graph = foldEventsToGraph([
      event({ span_id: "root", event_type: "CallAttempted", payload: { tool_name: "x" } }),
      event({ span_id: "root", event_type: "CallBlocked", payload: { reason: "amount > 100" } }),
    ]);
    expect(graph.status).toBe("had_blocks");
    expect(graph.blocked_calls).toBe(1);
    expect(graph.ended_at).not.toBeNull();
    expect(graph.nodes[0]).toMatchObject({ status: "blocked", reason: "amount > 100" });
  });

  it("CallCompleted carries latency/cost and completes the trace", () => {
    const graph = foldEventsToGraph([
      event({ span_id: "root", event_type: "CallAttempted", payload: { tool_name: "x" } }),
      event({
        span_id: "root",
        event_type: "CallCompleted",
        payload: { latency_ms: 42, cost: 0.01 },
      }),
    ]);
    expect(graph.status).toBe("completed");
    expect(graph.total_cost).toBeCloseTo(0.01);
    expect(graph.nodes[0]).toMatchObject({ status: "completed", latency_ms: 42, cost: 0.01 });
  });

  it("CallFailed sets reason from `error`, not `reason`, and fails the trace", () => {
    const graph = foldEventsToGraph([
      event({ span_id: "root", event_type: "CallAttempted", payload: { tool_name: "x" } }),
      event({ span_id: "root", event_type: "CallFailed", payload: { error: "timeout" } }),
    ]);
    expect(graph.status).toBe("failed");
    expect(graph.nodes[0]).toMatchObject({ status: "failed", reason: "timeout" });
  });

  it("child spans are linked as edges via parent_span_id, only the root ends the trace", () => {
    const graph = foldEventsToGraph([
      event({ span_id: "root", event_type: "CallAttempted", payload: { tool_name: "root" } }),
      event({
        span_id: "child",
        parent_span_id: "root",
        event_type: "CallAttempted",
        payload: { tool_name: "child" },
      }),
      event({ span_id: "child", event_type: "CallCompleted", payload: {} }),
    ]);
    // The child finished, but the root (which the trace's completion is
    // keyed on) never did — this must still read as running.
    expect(graph.status).toBe("running");
    expect(graph.ended_at).toBeNull();
    expect(graph.edges).toEqual([{ from: "root", to: "child" }]);
  });

  it("an event for an unknown span is ignored defensively, not thrown", () => {
    const graph = foldEventsToGraph([
      event({ span_id: "root", event_type: "CallAttempted", payload: { tool_name: "x" } }),
      event({ span_id: "never-created", event_type: "CallCompleted", payload: {} }),
    ]);
    expect(graph.nodes).toHaveLength(1);
  });

  it("ApprovalDenied behaves exactly like CallBlocked (status + reason)", () => {
    const graph = foldEventsToGraph([
      event({ span_id: "root", event_type: "CallAttempted", payload: { tool_name: "x" } }),
      event({
        span_id: "root",
        event_type: "ApprovalDenied",
        payload: { reason: "human said no" },
      }),
    ]);
    expect(graph.nodes[0]).toMatchObject({ status: "blocked", reason: "human said no" });
    expect(graph.status).toBe("had_blocks");
  });
});
