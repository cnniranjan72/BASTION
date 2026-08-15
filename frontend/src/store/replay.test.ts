import { beforeEach, describe, expect, it } from "vitest";
import type { RawEvent } from "../api/types";
import { useReplayStore } from "./replay";

function events(n: number): RawEvent[] {
  return Array.from({ length: n }, (_, i) => ({
    event_id: `evt-${i}`,
    trace_id: "trace-1",
    span_id: "span-1",
    parent_span_id: null,
    agent_id: "agent-1",
    event_type: "CallAttempted",
    payload: {},
    sequence_number: i,
    created_at: `2026-01-01T00:00:0${i}.000Z`,
  }));
}

beforeEach(() => {
  useReplayStore.getState().restart();
  useReplayStore.setState({ traceId: null, events: [], speed: 1 });
});

describe("useReplayStore", () => {
  it("load resets the playhead to -1 (nothing shown) and stops playback", () => {
    useReplayStore.getState().play();
    useReplayStore.getState().load("trace-1", events(3));

    const state = useReplayStore.getState();
    expect(state.traceId).toBe("trace-1");
    expect(state.events).toHaveLength(3);
    expect(state.playheadIndex).toBe(-1);
    expect(state.playing).toBe(false);
  });

  it("setPlayheadIndex clamps to [-1, events.length - 1]", () => {
    useReplayStore.getState().load("trace-1", events(3));

    useReplayStore.getState().setPlayheadIndex(100);
    expect(useReplayStore.getState().playheadIndex).toBe(2);

    useReplayStore.getState().setPlayheadIndex(-100);
    expect(useReplayStore.getState().playheadIndex).toBe(-1);
  });

  it("play restarts from the top if replay already finished, instead of silently doing nothing", () => {
    useReplayStore.getState().load("trace-1", events(3));
    useReplayStore.getState().setPlayheadIndex(2); // fully played

    useReplayStore.getState().play();

    expect(useReplayStore.getState().playheadIndex).toBe(-1);
    expect(useReplayStore.getState().playing).toBe(true);
  });

  it("play resumes from the current position if not yet finished", () => {
    useReplayStore.getState().load("trace-1", events(5));
    useReplayStore.getState().setPlayheadIndex(1);

    useReplayStore.getState().play();

    expect(useReplayStore.getState().playheadIndex).toBe(1);
    expect(useReplayStore.getState().playing).toBe(true);
  });

  it("pause stops playback without touching the playhead", () => {
    useReplayStore.getState().load("trace-1", events(3));
    useReplayStore.getState().setPlayheadIndex(1);
    useReplayStore.getState().play();

    useReplayStore.getState().pause();

    expect(useReplayStore.getState().playing).toBe(false);
    expect(useReplayStore.getState().playheadIndex).toBe(1);
  });

  it("restart resets the playhead and stops playback", () => {
    useReplayStore.getState().load("trace-1", events(3));
    useReplayStore.getState().setPlayheadIndex(2);
    useReplayStore.getState().play();

    useReplayStore.getState().restart();

    expect(useReplayStore.getState().playheadIndex).toBe(-1);
    expect(useReplayStore.getState().playing).toBe(false);
  });

  it("setSpeed updates speed without touching anything else", () => {
    useReplayStore.getState().load("trace-1", events(3));
    useReplayStore.getState().setPlayheadIndex(1);

    useReplayStore.getState().setSpeed(4);

    const state = useReplayStore.getState();
    expect(state.speed).toBe(4);
    expect(state.playheadIndex).toBe(1);
  });
});
