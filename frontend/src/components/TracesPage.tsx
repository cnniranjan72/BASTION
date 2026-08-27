import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { TRACE_STATUS_DESCRIPTION, TRACE_STATUS_LABEL } from "../lib/labels";
import { TableSkeleton } from "./TableSkeleton";
import { TopBar } from "./TopBar";
import type { Agent, Policy, TraceFilters, TraceStatus, TraceSummary } from "../api/types";

const STATUS_FILTERS: Array<{ value: TraceStatus | "all"; label: string }> = [
  { value: "all", label: "All statuses" },
  { value: "running", label: TRACE_STATUS_LABEL.running },
  { value: "completed", label: TRACE_STATUS_LABEL.completed },
  { value: "had_blocks", label: TRACE_STATUS_LABEL.had_blocks },
  { value: "failed", label: TRACE_STATUS_LABEL.failed },
];

// U16 (v2 upgrade): Trace Explorer. Filters are now real GET /traces query
// params (API_SPEC.md previously flagged agent_id/status/tool/policy/time
// range as "not implemented yet") -- a Jaeger/Datadog-style search against
// the actual backend, not client-side re-filtering of an already-fetched
// list. Free-text search over trace ID stays client-side (there's no
// meaningful server-side "search" for a UUID prefix).
export function TracesPage() {
  const navigate = useNavigate();
  // U16 (v2 upgrade): a real deep link, not decorative -- the command
  // palette's "Show blocked calls" action navigates to
  // /traces?status=had_blocks, and this page actually reads it on mount.
  const [searchParams] = useSearchParams();
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<TraceStatus | "all">(
    (searchParams.get("status") as TraceStatus | null) ?? "all",
  );
  const [agentId, setAgentId] = useState("all");
  // Same real-deep-link reasoning as status above -- Command Center's new
  // Track 01 purchase-stat cards link here with a tool filter pre-applied.
  const [tool, setTool] = useState(searchParams.get("tool") ?? "all");
  const [policyName, setPolicyName] = useState("all");
  const [startedAfter, setStartedAfter] = useState("");

  useEffect(() => {
    Promise.all([api.listAgents(), api.listPolicies()])
      .then(([agentList, policyList]) => {
        setAgents(agentList);
        setPolicies(policyList);
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load filters");
      });
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    const filters: TraceFilters = {};
    if (agentId !== "all") filters.agent_id = agentId;
    if (status !== "all") filters.status = status;
    if (tool !== "all") filters.tool = tool;
    if (policyName !== "all") filters.policy = policyName;
    if (startedAfter) filters.started_after = new Date(startedAfter).toISOString();

    api
      .listTraces(filters)
      .then(setTraces)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load traces");
      })
      .finally(() => setLoading(false));
  }, [agentId, status, tool, policyName, startedAfter]);

  const agentName = useMemo(() => {
    const byId = new Map(agents.map((a) => [a.id, a.name]));
    return (id: string) => byId.get(id) ?? id.slice(0, 8);
  }, [agents]);

  // Tool names aren't a closed/enumerable set anywhere in the backend (no
  // "tools" table -- a tool_name is just whatever string an agent's SDK
  // call passes, and most of a policy's tools are only ever reached via a
  // wildcard "*" rule, never named explicitly). knownTools is therefore
  // autocomplete *suggestions* from real policy rule names, not an
  // exhaustive restriction -- the tool filter itself is free text, so a
  // trace whose tool was never named in any policy rule is still findable.
  const knownTools = useMemo(() => {
    const set = new Set<string>();
    for (const p of policies) {
      for (const rule of p.definition) {
        if (rule.match.tool !== "*") set.add(rule.match.tool);
      }
    }
    return Array.from(set).sort();
  }, [policies]);

  const knownPolicyNames = useMemo(
    () => Array.from(new Set(policies.map((p) => p.name))).sort(),
    [policies],
  );

  const filtered = query.trim()
    ? traces.filter((t) => {
        const q = query.trim().toLowerCase();
        return (
          t.trace_id.toLowerCase().includes(q) || agentName(t.agent_id).toLowerCase().includes(q)
        );
      })
    : traces;

  const blockedTotal = filtered.reduce((sum, t) => sum + t.blocked_calls, 0);

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page page--wide">
        <div className="page__header">
          <h1>Trace Explorer</h1>
          <p className="page__subtitle">
            Every trace BASTION has recorded, searchable and filterable by agent, status, tool,
            policy, and time — pick one to replay its full causal graph.
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
          <input
            className="filter-bar__tool"
            list="known-tools"
            placeholder="Any tool (e.g. payments.transfer)"
            value={tool === "all" ? "" : tool}
            onChange={(e) => setTool(e.target.value.trim() || "all")}
          />
          <datalist id="known-tools">
            {knownTools.map((t) => (
              <option key={t} value={t} />
            ))}
          </datalist>
          <select value={policyName} onChange={(e) => setPolicyName(e.target.value)}>
            <option value="all">All policies</option>
            {knownPolicyNames.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <input
            type="date"
            value={startedAfter}
            onChange={(e) => setStartedAfter(e.target.value)}
            title="Started after"
          />
          <span className="filter-bar__count">
            {filtered.length} trace{filtered.length === 1 ? "" : "s"}
            {blockedTotal > 0 && ` · ${blockedTotal} blocked call${blockedTotal === 1 ? "" : "s"}`}
          </span>
        </div>

        <div className="status-legend">
          {(Object.keys(TRACE_STATUS_LABEL) as TraceStatus[]).map((s) => (
            <span key={s} className="status-legend__item" title={TRACE_STATUS_DESCRIPTION[s]}>
              <span className={`status-legend__dot status-legend__dot--${s}`} />
              {TRACE_STATUS_LABEL[s]}
            </span>
          ))}
        </div>

        {loading ? (
          <TableSkeleton />
        ) : filtered.length === 0 ? (
          <p className="page__empty">
            {traces.length === 0 ? "No traces match these filters." : "No traces match this search."}
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
                  <tr key={t.trace_id} onClick={() => navigate(`/replay/${t.trace_id}`)}>
                    <td>
                      <span
                        className={`badge badge--status-${t.status}`}
                        title={TRACE_STATUS_DESCRIPTION[t.status]}
                      >
                        {TRACE_STATUS_LABEL[t.status]}
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
