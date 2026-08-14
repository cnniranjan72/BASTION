import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useGraphStore } from "../store/graph";
import { useLiveGraph } from "../hooks/useLiveGraph";
import { GraphCanvas } from "./GraphView/GraphCanvas";
import { InspectorPanel } from "./InspectorPanel";
import { TopBar } from "./TopBar";
import type { Agent, TraceSummary } from "../api/types";

type ViewMode =
  { kind: "idle" } | { kind: "live"; agentId: string } | { kind: "replay"; traceId: string };

export function Dashboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [mode, setMode] = useState<ViewMode>({ kind: "idle" });
  const [agentIdInput, setAgentIdInput] = useState("");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentsLoaded, setAgentsLoaded] = useState(false);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [tracesError, setTracesError] = useState<string | null>(null);
  const loadSnapshot = useGraphStore((s) => s.loadSnapshot);
  const reset = useGraphStore((s) => s.reset);

  const liveStatus = useLiveGraph(mode.kind === "live" ? mode.agentId : null);

  useEffect(() => {
    api
      .listAgents()
      .then(setAgents)
      .catch(() => {
        // Non-fatal — the picker just falls back to manual agent_id entry.
      })
      .finally(() => setAgentsLoaded(true));
  }, []);

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

  // Deep link from the Traces page (/graph?trace=<id>) — open replay once,
  // then drop the query param so it doesn't re-fire on later state changes.
  useEffect(() => {
    const traceId = searchParams.get("trace");
    if (traceId) {
      openReplay(traceId);
      setSearchParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  return (
    <div className="dashboard">
      <TopBar liveStatus={mode.kind === "live" ? liveStatus : null} />
      <div className="dashboard__body">
        <aside className="sidebar">
          <section>
            <h2>Live view</h2>
            {agentsLoaded && agents.length === 0 ? (
              <p className="sidebar__empty">
                No agents yet. <Link to="/agents">Create one</Link> to see it here.
              </p>
            ) : (
              <div className="sidebar__live-form">
                {agents.length > 0 ? (
                  <select value={agentIdInput} onChange={(e) => setAgentIdInput(e.target.value)}>
                    <option value="">Choose an agent…</option>
                    {agents.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    placeholder="agent_id"
                    value={agentIdInput}
                    onChange={(e) => setAgentIdInput(e.target.value)}
                  />
                )}
                <button onClick={goLive}>Connect</button>
              </div>
            )}
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
              {agentsLoaded && agents.length === 0 ? (
                <>
                  Nothing to show yet — <Link to="/agents">create your first agent</Link> to get an
                  API key, point it at BASTION, and its calls will show up here live.
                </>
              ) : (
                "Choose an agent and connect, or pick a trace to replay."
              )}
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
