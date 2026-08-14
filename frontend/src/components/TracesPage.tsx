import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { TableSkeleton } from "./TableSkeleton";
import { TopBar } from "./TopBar";
import type { Agent, TraceStatus, TraceSummary } from "../api/types";

const STATUS_FILTERS: Array<{ value: TraceStatus | "all"; label: string }> = [
  { value: "all", label: "All statuses" },
  { value: "running", label: "Running" },
  { value: "completed", label: "Completed" },
  { value: "had_blocks", label: "Had blocks" },
  { value: "failed", label: "Failed" },
];

export function TracesPage() {
  const navigate = useNavigate();
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<TraceStatus | "all">("all");
  const [agentId, setAgentId] = useState("all");

  useEffect(() => {
    Promise.all([api.listTraces(), api.listAgents()])
      .then(([traceList, agentList]) => {
        setTraces(traceList);
        setAgents(agentList);
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load traces");
      })
      .finally(() => setLoading(false));
  }, []);

  const agentName = useMemo(() => {
    const byId = new Map(agents.map((a) => [a.id, a.name]));
    return (id: string) => byId.get(id) ?? id.slice(0, 8);
  }, [agents]);

  const filtered = traces.filter((t) => {
    if (status !== "all" && t.status !== status) return false;
    if (agentId !== "all" && t.agent_id !== agentId) return false;
    if (query.trim()) {
      const q = query.trim().toLowerCase();
      if (!t.trace_id.toLowerCase().includes(q) && !agentName(t.agent_id).toLowerCase().includes(q)) {
        return false;
      }
    }
    return true;
  });

  const blockedTotal = filtered.reduce((sum, t) => sum + t.blocked_calls, 0);

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page page--wide">
        <div className="page__header">
          <h1>Traces</h1>
          <p className="page__subtitle">
            Every trace BASTION has recorded, searchable and filterable — pick one to replay its
            full causal graph.
          </p>
        </div>

        {error && <p className="page__error">{error}</p>}

        <div className="filter-bar">
          <input
            className="filter-bar__search"
            placeholder="Search by trace ID or agent…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select value={status} onChange={(e) => setStatus(e.target.value as TraceStatus | "all")}>
            {STATUS_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
          <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
            <option value="all">All agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          <span className="filter-bar__count">
            {filtered.length} trace{filtered.length === 1 ? "" : "s"}
            {blockedTotal > 0 && ` · ${blockedTotal} blocked call${blockedTotal === 1 ? "" : "s"}`}
          </span>
        </div>

        {loading ? (
          <TableSkeleton />
        ) : filtered.length === 0 ? (
          <p className="page__empty">
            {traces.length === 0 ? "No traces recorded yet." : "No traces match these filters."}
          </p>
        ) : (
          <div className="table-scroll">
            <table className="data-table data-table--clickable">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Agent</th>
                  <th>Trace ID</th>
                  <th>Calls</th>
                  <th>Blocked</th>
                  <th>Cost</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((t) => (
                  <tr key={t.trace_id} onClick={() => navigate(`/graph?trace=${t.trace_id}`)}>
                    <td>
                      <span className={`badge badge--status-${t.status}`}>
                        {t.status.replace("_", " ")}
                      </span>
                    </td>
                    <td>{agentName(t.agent_id)}</td>
                    <td className="data-table__mono">{t.trace_id.slice(0, 8)}</td>
                    <td>{t.total_calls}</td>
                    <td>{t.blocked_calls > 0 ? t.blocked_calls : "—"}</td>
                    <td>${t.total_cost.toFixed(4)}</td>
                    <td>{new Date(t.started_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
