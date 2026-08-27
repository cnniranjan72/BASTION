import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useLiveGraph } from "../hooks/useLiveGraph";
import { TRACE_STATUS_LABEL } from "../lib/labels";
import { GraphCanvas } from "./GraphView/GraphCanvas";
import { GraphErrorBoundary } from "./GraphView/GraphErrorBoundary";
import { GraphLegend } from "./GraphView/GraphLegend";
import { TimelineStrip } from "./GraphView/TimelineStrip";
import { InspectorPanel } from "./InspectorPanel";
import { TopBar } from "./TopBar";
import type { Agent, TraceSummary } from "../api/types";

/** U15 (v2 upgrade): the Live Execution Graph flagship screen, split out
 * from v1's Dashboard, which conflated live viewing with a static
 * final-state "replay" (just loadSnapshot + render once, no timeline, no
 * animation). Historical replay is now IncidentReplayPage — a genuinely
 * different experience (step-through with real timestamps), not a mode
 * of this one. This page is live-only. */
export function Dashboard() {
  const [agentId, setAgentId] = useState<string | null>(null);
  const [agentIdInput, setAgentIdInput] = useState("");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentsLoaded, setAgentsLoaded] = useState(false);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [tracesError, setTracesError] = useState<string | null>(null);

  const liveStatus = useLiveGraph(agentId);

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
  }, [agentId]);

  function goLive() {
    if (!agentIdInput.trim()) return;
    setAgentId(agentIdInput.trim());
  }

  return (
    <div className="dashboard">
      <TopBar liveStatus={agentId ? liveStatus : null} />
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
            <p className="sidebar__hint">Replay a past trace step-by-step in Incident Replay.</p>
            {tracesError && <p className="sidebar__error">{tracesError}</p>}
            <ul className="trace-list">
              {traces.map((trace) => (
                <li key={trace.trace_id}>
                  <Link
                    className={`trace-list__item trace-list__item--${trace.status}`}
                    to={`/replay/${trace.trace_id}`}
                  >
                    <span className="trace-list__status">{TRACE_STATUS_LABEL[trace.status]}</span>
                    <span className="trace-list__meta">
                      {trace.total_calls} calls · {trace.blocked_calls} blocked
                    </span>
                  </Link>
                </li>
              ))}
              {traces.length === 0 && !tracesError && (
                <li className="trace-list__empty">No completed traces yet.</li>
              )}
            </ul>
          </section>
        </aside>

        <div className="graph-area-wrap">
          <main className="graph-area">
            {!agentId ? (
              <div className="graph-area__placeholder">
                {agentsLoaded && agents.length === 0 ? (
                  <>
                    Nothing to show yet — <Link to="/agents">create your first agent</Link> to get
                    an API key, point it at BASTION, and its calls will show up here live.
                  </>
                ) : (
                  "Choose an agent and connect to watch it run live."
                )}
              </div>
            ) : (
              <GraphErrorBoundary>
                <GraphCanvas />
                <GraphLegend />
              </GraphErrorBoundary>
            )}
          </main>
          {agentId && <TimelineStrip />}
        </div>

        <InspectorPanel agentId={agentId} />
      </div>
    </div>
  );
}
