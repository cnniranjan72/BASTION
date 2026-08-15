import { create } from "zustand";
import type { RawEvent } from "../api/types";

interface ReplayState {
  traceId: string | null;
  events: RawEvent[];
  playheadIndex: number; // -1 = nothing shown yet, events.length - 1 = fully played
  playing: boolean;
  speed: number;

  load: (traceId: string, events: RawEvent[]) => void;
  setPlayheadIndex: (index: number) => void;
  play: () => void;
  pause: () => void;
  restart: () => void;
  setSpeed: (speed: number) => void;
}

/** U15 (v2 upgrade), Incident Replay's own store — deliberately separate
 * from store/graph.ts (the live view's store). The two never need to be
 * open at once (a client is either watching one agent live or replaying
 * one past trace), but conflating them would tangle "delta from a live
 * WS message" logic with "re-fold a prefix of a fixed, known event list"
 * logic for no benefit — this store only ever holds the ordered raw
 * events and a playhead; IncidentReplayPage derives the rendered graph
 * from foldEvents.ts and pushes it into the *existing* graph store's
 * `loadSnapshot`, reusing every 3D rendering component unchanged. */
export const useReplayStore = create<ReplayState>((set, get) => ({
  traceId: null,
  events: [],
  playheadIndex: -1,
  playing: false,
  speed: 1,

  load: (traceId, events) => set({ traceId, events, playheadIndex: -1, playing: false }),
  setPlayheadIndex: (index) =>
    set({ playheadIndex: Math.max(-1, Math.min(index, get().events.length - 1)) }),
  play: () =>
    set((state) => ({
      // Restart from the top if replay already finished — pressing play
      // again should replay, not silently do nothing.
      playheadIndex: state.playheadIndex >= state.events.length - 1 ? -1 : state.playheadIndex,
      playing: true,
    })),
  pause: () => set({ playing: false }),
  restart: () => set({ playheadIndex: -1, playing: false }),
  setSpeed: (speed) => set({ speed }),
}));
