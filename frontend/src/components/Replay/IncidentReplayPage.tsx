import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../../api/client";
import { useGraphStore } from "../../store/graph";
import { useReplayStore } from "../../store/replay";
import { foldEventsToGraph } from "../../lib/foldEvents";
import { GraphCanvas } from "../GraphView/GraphCanvas";
import { GraphErrorBoundary } from "../GraphView/GraphErrorBoundary";
import { GraphLegend } from "../GraphView/GraphLegend";
import { InspectorPanel } from "../InspectorPanel";
import { TopBar } from "../TopBar";
import { ReplayTimeline } from "./ReplayTimeline";

/** U15 (v2 upgrade), FRONTEND_V2.md's Incident Replay flagship: "pick a
 * past trace/incident, hit replay, and watch the execution graph animate
 * through the exact original sequence with real timestamps." Reconstructed
 * purely from GET /traces/{id}/events (the immutable event log) — no
 * separate replay-data storage, per that section's explicit requirement.
 *
 * Reuses the Live Execution Graph's own 3D rendering stack unchanged:
 * every scrub/play step re-folds events[0..playhead] (foldEvents.ts, a
 * client-side port of the backend's own fold) into a TraceGraph and pushes
 * it through the exact same loadSnapshot() the live view's static
 * final-state loads already used — "one shape, three consumers" (graph.py's
 * own docstring) holds on the frontend too. */
export function IncidentReplayPage() {
  const { traceId } = useParams<{ traceId: string }>();
  const loadSnapshot = useGraphStore((s) => s.loadSnapshot);
  const reset = useGraphStore((s) => s.reset);
  const load = useReplayStore((s) => s.load);
  const events = useReplayStore((s) => s.events);
  const playheadIndex = useReplayStore((s) => s.playheadIndex);
  const playing = useReplayStore((s) => s.playing);
  const speed = useReplayStore((s) => s.speed);
  const setPlayheadIndex = useReplayStore((s) => s.setPlayheadIndex);
  const [error, setError] = useState<string | null>(null);
  const [agentId, setAgentId] = useState<string | null>(null);

  useEffect(() => {
    if (!traceId) return;
    reset();
    api
      .getTraceEvents(traceId)
      .then((fetched) => {
        if (fetched.length === 0) {
          setError("This trace has no recorded events.");
          return;
        }
        load(traceId, fetched);
        setAgentId(fetched[0]!.agent_id);
      })
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : "Failed to load"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [traceId]);

  // Re-fold and push into the graph store on every playhead move — this
  // is what "animates through the exact original sequence" actually is:
  // a series of full re-derivations, not incremental patching, since a
  // scrub can jump backward as easily as forward.
  useEffect(() => {
    if (playheadIndex < 0 || events.length === 0) {
      reset();
      return;
    }
    const graph = foldEventsToGraph(events.slice(0, playheadIndex + 1));
    loadSnapshot(graph);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playheadIndex, events]);

  // Playback loop: advance the playhead on a timer scaled to the real
  // inter-event gaps (divided by speed) — a policy decision 8ms after a
  // call attempt animates almost instantly; a multi-second gap animates
  // as a multi-second wait, exactly matching the original run's own pacing.
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!playing || events.length === 0) return;
    if (playheadIndex >= events.length - 1) return;
    const current = events[Math.max(playheadIndex, 0)]!;
    const next = events[playheadIndex + 1]!;
    const realDelayMs = Math.max(0, Date.parse(next.created_at) - Date.parse(current.created_at));
    // Cap the wait so a real multi-minute gap between calls doesn't stall
    // playback for a literal minute — replay should feel watchable, not
    // be a byte-for-byte timing replica.
    const delay = Math.min(realDelayMs, 3000) / speed;
    timerRef.current = setTimeout(() => setPlayheadIndex(playheadIndex + 1), delay);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [playing, playheadIndex, events, speed, setPlayheadIndex]);

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="dashboard__body dashboard__body--replay">
        <div className="graph-area-wrap">
          <main className="graph-area">
            {error ? (
              <div className="graph-area__placeholder">{error}</div>
            ) : events.length === 0 ? (
              <div className="graph-area__placeholder">Loading trace…</div>
            ) : (
              <GraphErrorBoundary>
                <GraphCanvas />
                <GraphLegend />
              </GraphErrorBoundary>
            )}
          </main>
          <ReplayTimeline />
        </div>
        <InspectorPanel agentId={agentId} />
      </div>
    </div>
  );
}
