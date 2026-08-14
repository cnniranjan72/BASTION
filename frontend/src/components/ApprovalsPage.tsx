import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useAuthStore } from "../store/auth";
import { TopBar } from "./TopBar";
import type { ApprovalRequest } from "../api/types";

function canResolve(role: string | null): boolean {
  return role === "owner" || role === "admin" || role === "approver";
}

export function ApprovalsPage() {
  const role = useAuthStore((s) => s.role);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resolving, setResolving] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setApprovals(await api.listApprovals());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load approvals");
    } finally {
      setLoading(false);
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
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Failed to ${decision}`);
    } finally {
      setResolving(null);
    }
  }

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page">
        <div className="page__header">
          <h1>Approvals</h1>
          <p className="page__subtitle">
            Calls a policy routed to a human. An agent is paused, waiting on each of these.
          </p>
        </div>

        {error && <p className="page__error">{error}</p>}

        {loading ? (
          <p className="page__empty">Loading…</p>
        ) : approvals.length === 0 ? (
          <p className="page__empty">No pending approvals.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Requested</th>
                <th>Trace</th>
                <th>Span</th>
                {canResolve(role) && <th />}
              </tr>
            </thead>
            <tbody>
              {approvals.map((a) => (
                <tr key={a.id}>
                  <td>{new Date(a.requested_at).toLocaleString()}</td>
                  <td className="data-table__mono">{a.trace_id}</td>
                  <td className="data-table__mono">{a.span_id}</td>
                  {canResolve(role) && (
                    <td className="data-table__actions">
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
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
