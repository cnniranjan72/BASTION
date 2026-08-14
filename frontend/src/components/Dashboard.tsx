import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useGraphStore } from "../store/graph";
import { useLiveGraph } from "../hooks/useLiveGraph";
import { GraphCanvas } from "./GraphView/GraphCanvas";
import { InspectorPanel } from "./InspectorPanel";
import { TopBar } from "./TopBar";
import type { TraceSummary } from "../api/types";

type ViewMode =
  { kind: "idle" } | { kind: "live"; agentId: string } | { kind: "replay"; traceId: string };

export function Dashboard() {
  const [mode, setMode] = useState<ViewMode>({ kind: "idle" });
  const [agentIdInput, setAgentIdInput] = useState("");
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [tracesError, setTracesError] = useState<string | null>(null);
  const loadSnapshot = useGraphStore((s) => s.loadSnapshot);
  const reset = useGraphStore((s) => s.reset);

  const liveStatus = useLiveGraph(mode.kind === "live" ? mode.agentId : null);

  useEffect(() => {
    let cancelled = false;
    api
      .listTraces()
      .then((data) => {
        if (!cancelled) setTraces(data);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setTracesError(err instanceof ApiError ? err.message : "Failed to load traces");
      });
    return () => {
      cancelled = true;
    };
    // Re-fetch whenever we switch views — a trace that just finished (live
    // -> completed) won't show up in the list until we ask again.
  }, [mode]);

  async function openReplay(traceId: string) {
    try {
      const graph = await api.getTrace(traceId);
      loadSnapshot(graph);
      setMode({ kind: "replay", traceId });
    } catch (err) {
      setTracesError(err instanceof ApiError ? err.message : "Failed to load trace");
    }
  }

  function goLive() {
    if (!agentIdInput.trim()) return;
    reset();
    setMode({ kind: "live", agentId: agentIdInput.trim() });
  }

  return (
    <div className="dashboard">
      <TopBar liveStatus={mode.kind === "live" ? liveStatus : null} />
      <div className="dashboard__body">
        <aside className="sidebar">
          <section>
            <h2>Live view</h2>
            <div className="sidebar__live-form">
              <input
                placeholder="agent_id"
                value={agentIdInput}
                onChange={(e) => setAgentIdInput(e.target.value)}
              />
              <button onClick={goLive}>Connect</button>
            </div>
          </section>

          <section>
            <h2>Recent traces</h2>
            {tracesError && <p className="sidebar__error">{tracesError}</p>}
            <ul className="trace-list">
              {traces.map((trace) => (
                <li key={trace.trace_id}>
                  <button
                    className={`trace-list__item trace-list__item--${trace.status} ${
                      mode.kind === "replay" && mode.traceId === trace.trace_id ? "is-active" : ""
                    }`}
                    onClick={() => openReplay(trace.trace_id)}
                  >
                    <span className="trace-list__status">{trace.status}</span>
                    <span className="trace-list__meta">
                      {trace.total_calls} calls · {trace.blocked_calls} blocked
                    </span>
                  </button>
                </li>
              ))}
              {traces.length === 0 && !tracesError && (
                <li className="trace-list__empty">No completed traces yet.</li>
              )}
            </ul>
          </section>
        </aside>

        <main className="graph-area">
          {mode.kind === "idle" ? (
            <div className="graph-area__placeholder">
              Enter an agent_id and connect, or pick a trace to replay.
            </div>
          ) : (
            <GraphCanvas />
          )}
        </main>

        <InspectorPanel />
      </div>
    </div>
  );
}
