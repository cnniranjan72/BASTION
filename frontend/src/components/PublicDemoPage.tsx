import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useGraphStore } from "../store/graph";
import { useReplayStore } from "../store/replay";
import { foldEventsToGraph } from "../lib/foldEvents";
import { GraphCanvas } from "./GraphView/GraphCanvas";
import { GraphErrorBoundary } from "./GraphView/GraphErrorBoundary";
import { GraphLegend } from "./GraphView/GraphLegend";
import { InspectorPanel } from "./InspectorPanel";
import { ReplayTimeline } from "./Replay/ReplayTimeline";
import type { RawEvent } from "../api/types";

const DEMO_TRACE_ID = "dd707b73-e30b-4f45-b7b0-a365213962bd";

/** Public, logged-out replay of one real, already-happened trace -- the
 * shortest path from a cold landing-page visit to "watch it block an
 * attack," with no signup, no SDK, no code to run. Reuses
 * IncidentReplayPage's exact rendering path (same fold, same 3D graph,
 * same timeline, same inspector) unchanged, fed from a static export
 * (frontend/scripts/export_demo_trace.py, frontend/public/demo-trace-
 * events.json) of a real trace instead of an authenticated
 * GET /traces/{id}/events call -- no new backend capability, and no
 * credentials of any kind ship to this public page. */
export function PublicDemoPage() {
  const loadSnapshot = useGraphStore((s) => s.loadSnapshot);
  const reset = useGraphStore((s) => s.reset);
  const load = useReplayStore((s) => s.load);
  const play = useReplayStore((s) => s.play);
  const events = useReplayStore((s) => s.events);
  const playheadIndex = useReplayStore((s) => s.playheadIndex);
  const playing = useReplayStore((s) => s.playing);
  const speed = useReplayStore((s) => s.speed);
  const setPlayheadIndex = useReplayStore((s) => s.setPlayheadIndex);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    reset();
    fetch("/demo-trace-events.json")
      .then((res) => {
        if (!res.ok) throw new Error(String(res.status));
        return res.json() as Promise<RawEvent[]>;
      })
      .then((fetched) => {
        load(DEMO_TRACE_ID, fetched);
        play();
      })
      .catch(() => setError("Couldn't load the demo trace."));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Same re-fold-on-every-playhead-move as IncidentReplayPage.
  useEffect(() => {
    if (playheadIndex < 0 || events.length === 0) {
      reset();
      return;
    }
    const graph = foldEventsToGraph(events.slice(0, playheadIndex + 1));
    loadSnapshot(graph);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playheadIndex, events]);

  // Same playback loop as IncidentReplayPage, capped a little tighter
  // (1.5s not 3s) -- a cold visitor giving this a minute shouldn't sit
  // through a long real gap between two calls.
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!playing || events.length === 0) return;
    if (playheadIndex >= events.length - 1) return;
    const current = events[Math.max(playheadIndex, 0)]!;
    const next = events[playheadIndex + 1]!;
    const realDelayMs = Math.max(0, Date.parse(next.created_at) - Date.parse(current.created_at));
    const delay = Math.min(realDelayMs, 1500) / speed;
    timerRef.current = setTimeout(() => setPlayheadIndex(playheadIndex + 1), delay);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [playing, playheadIndex, events, speed, setPlayheadIndex]);

  return (
    <div className="dashboard">
      <header className="public-demo__header">
        <Link to="/" className="landing__wordmark">
          <span className="landing__wordmark-dot" /> BASTION
        </Link>
        <nav className="landing__nav-links">
          <Link to="/login">Sign in</Link>
          <Link to="/signup" className="landing__nav-cta">
            Get started
          </Link>
        </nav>
      </header>
      <p className="public-demo__caption">
        A real trace that already happened, not a mockup: a support ticket carries an injected
        instruction to transfer $500 to an attacker — blocked — while a legitimate $25 refund in
        the same trace goes through. Playing automatically; scrub or click a node any time.
      </p>
      <div className="dashboard__body dashboard__body--replay">
        <div className="graph-area-wrap">
          <main className="graph-area">
            {error ? (
              <div className="graph-area__placeholder">{error}</div>
            ) : events.length === 0 ? (
              <div className="graph-area__placeholder">Loading the real trace…</div>
            ) : (
              <GraphErrorBoundary>
                <GraphCanvas />
                <GraphLegend />
              </GraphErrorBoundary>
            )}
          </main>
          <ReplayTimeline />
        </div>
        <InspectorPanel agentId={events[0]?.agent_id ?? null} />
      </div>
    </div>
  );
}
