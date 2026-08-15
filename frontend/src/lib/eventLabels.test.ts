import { describe, expect, it } from "vitest";
import type { RawEvent } from "../api/types";
import { describeEvent, formatRelativeTime } from "./eventLabels";

function event(event_type: string, payload: Record<string, unknown> = {}): RawEvent {
  return {
    event_id: "evt-1",
    trace_id: "trace-1",
    span_id: "span-1",
    parent_span_id: null,
    agent_id: "agent-1",
    event_type,
    payload,
    sequence_number: 1,
    created_at: "2026-01-01T00:00:00.000Z",
  };
}

describe("describeEvent", () => {
  it("describes CallAttempted with the tool name", () => {
    expect(describeEvent(event("CallAttempted", { tool_name: "payments.transfer" }))).toBe(
      "Agent requests payments.transfer",
    );
  });

  it("falls back to 'unknown tool' when tool_name is missing", () => {
    expect(describeEvent(event("CallAttempted", {}))).toBe("Agent requests unknown tool");
  });

  it("includes the block reason", () => {
    expect(describeEvent(event("CallBlocked", { reason: "amount > 100" }))).toBe(
      "BLOCKED — amount > 100",
    );
  });

  it("formats CallCompleted's latency to 0 decimal places", () => {
    expect(describeEvent(event("CallCompleted", { latency_ms: 42.7 }))).toBe("Completed (43ms)");
  });

  it("omits the latency parenthetical when latency_ms is absent", () => {
    expect(describeEvent(event("CallCompleted", {}))).toBe("Completed");
  });

  it("uses `error`, not `reason`, for CallFailed", () => {
    expect(describeEvent(event("CallFailed", { error: "timeout", reason: "wrong field" }))).toBe(
      "Failed — timeout",
    );
  });

  it("falls back to the raw event_type for an unrecognized type", () => {
    expect(describeEvent(event("SomeFutureEventType"))).toBe("SomeFutureEventType");
  });
});

describe("formatRelativeTime", () => {
  it("formats under a minute", () => {
    expect(formatRelativeTime(3854)).toBe("00:03.854");
  });

  it("formats over a minute, still MM:SS.mmm", () => {
    expect(formatRelativeTime(65_432)).toBe("01:05.432");
  });

  it("formats zero", () => {
    expect(formatRelativeTime(0)).toBe("00:00.000");
  });

  it("pads single-digit minutes/seconds/millis correctly", () => {
    expect(formatRelativeTime(1_005)).toBe("00:01.005");
  });
});
