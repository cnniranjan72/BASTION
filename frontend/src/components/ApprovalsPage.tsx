import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useAuthStore } from "../store/auth";
import { toast } from "../store/toast";
import { EmptyState } from "./EmptyState";
import { ApprovalsIcon } from "./icons";
import { TableSkeleton } from "./TableSkeleton";
import { TopBar } from "./TopBar";
import type { ApprovalRequest, GraphNode, TraceGraph } from "../api/types";

function canResolve(role: string | null): boolean {
  return role === "owner" || role === "admin" || role === "approver";
}

// U16 (v2 upgrade), FRONTEND_V2.md's Approval Center: "each pending
// approval shows the causal trace leading up to it so the approver has
// context before deciding" -- not just trace_id/span_id as raw text.
// Fetches the real trace graph per pending approval (GET /traces/{id}) and
// pulls the pending span's own real tool_name/args/reason plus the other
// real calls already made in that same trace, ahead of the decision.
export function ApprovalsPage() {
  const role = useAuthStore((s) => s.role);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [traces, setTraces] = useState<Record<string, TraceGraph>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resolving, setResolving] = useState<string | null>(null);
  const loadedOnce = useRef(false);

  async function load() {
    if (!loadedOnce.current) setLoading(true);
    setError(null);
    try {
      const list = await api.listApprovals();
      setApprovals(list);
      const uniqueTraceIds = Array.from(new Set(list.map((a) => a.trace_id)));
      const results = await Promise.all(
        uniqueTraceIds.map((id) =>
          api
            .getTrace(id)
            .then((graph) => [id, graph] as const)
            .catch(() => null),
        ),
      );
      setTraces(Object.fromEntries(results.filter((r): r is [string, TraceGraph] => r !== null)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load approvals");
    } finally {
      setLoading(false);
      loadedOnce.current = true;
    }
  }

  useEffect(() => {
    load();
    // Approvals are time-sensitive (an agent is paused waiting on one) —
    // poll rather than requiring a manual refresh. A WS push would be
    // nicer but no /live channel exists for the approvals list itself.
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  async function resolve(id: string, decision: "approve" | "deny") {
    setError(null);
    setResolving(id);
    try {
      if (decision === "approve") await api.approve(id);
      else await api.deny(id);
      toast.success(decision === "approve" ? "Approved" : "Denied");
      await load();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : `Failed to ${decision}`;
      setError(message);
      toast.error(message);
    } finally {
      setResolving(null);
    }
  }

  function pendingNode(a: ApprovalRequest): GraphNode | null {
    return traces[a.trace_id]?.nodes.find((n) => n.span_id === a.span_id) ?? null;
  }

  function priorCalls(a: ApprovalRequest): GraphNode[] {
    const graph = traces[a.trace_id];
    if (!graph) return [];
    return graph.nodes.filter((n) => n.span_id !== a.span_id);
  }

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page">
        <div className="page__header">
          <h1>Approval Center</h1>
          <p className="page__subtitle">
            Calls a policy routed to a human. An agent is paused, waiting on each of these — the
            causal trace leading up to it is shown so you have context before deciding.
          </p>
        </div>

        {error && <p className="page__error">{error}</p>}

        {loading ? (
          <TableSkeleton />
        ) : approvals.length === 0 ? (
          <EmptyState icon={ApprovalsIcon} title="No pending approvals">
            When a policy routes a call to a human, it'll show up here for someone to approve or
            deny.
          </EmptyState>
        ) : (
          <ul className="approval-queue">
            {approvals.map((a) => {
              const node = pendingNode(a);
              const prior = priorCalls(a);
              return (
                <li key={a.id} className="approval-card">
                  <div className="approval-card__header">
                    <span className="approval-card__tool">
                      {node?.tool_name ?? "unknown tool"}
                    </span>
                    <span className="approval-card__requested">
                      requested {new Date(a.requested_at).toLocaleString()}
                    </span>
                  </div>

                  {node?.reason && <p className="approval-card__reason">{node.reason}</p>}

                  {node?.args && Object.keys(node.args).length > 0 && (
                    <pre className="approval-card__args">{JSON.stringify(node.args, null, 2)}</pre>
                  )}

                  {prior.length > 0 && (
                    <div className="approval-card__causal">
                      <span className="approval-card__causal-label">
                        Leading up to this ({prior.length} prior call{prior.length === 1 ? "" : "s"}):
                      </span>
                      <ul>
                        {prior.map((p) => (
                          <li key={p.span_id}>
                            <span className={`badge badge--status-${p.status}`}>{p.status}</span>{" "}
                            {p.tool_name}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <div className="approval-card__footer">
                    <Link to={`/replay/${a.trace_id}`} className="approval-card__link">
                      View full trace →
                    </Link>
                    {canResolve(role) && (
                      <div className="data-table__actions">
                        <button
                          className="button--approve"
                          onClick={() => resolve(a.id, "approve")}
                          disabled={resolving === a.id}
                        >
                          Approve
                        </button>
                        <button
                          className="button--deny"
                          onClick={() => resolve(a.id, "deny")}
                          disabled={resolving === a.id}
                        >
                          Deny
                        </button>
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
