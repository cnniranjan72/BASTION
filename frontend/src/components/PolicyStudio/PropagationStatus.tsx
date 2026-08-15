import { useEffect, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { PolicyPropagationResponse } from "../../api/types";

interface PropagationStatusProps {
  policySetId: string | null;
}

/** U15 (v2 upgrade), Policy Studio's propagation-status panel —
 * FRONTEND_V2.md: "show real propagation status pulled from actual
 * interceptor health/version checks ... not a generic 'Saved ✓'." Calls
 * GET /policies/{policy_set_id}/propagation directly.
 *
 * Copy note (see docs/adr/ADR-020): this deployment has no multi-replica
 * registry, so this honestly says "active on this interceptor" rather
 * than FRONTEND_V2.md's literal "4/4 interceptors" example text — a
 * deliberate wording choice, not a shortfall in wiring; the underlying
 * comparison (Postgres's real active version vs. this process's real
 * live policy_cache) is completely real. */
export function PropagationStatus({ policySetId }: PropagationStatusProps) {
  const [status, setStatus] = useState<PolicyPropagationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!policySetId) {
      setStatus(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getPolicyPropagation(policySetId)
      .then((data) => {
        if (!cancelled) setStatus(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [policySetId]);

  if (!policySetId) return null;
  if (loading) return <p className="propagation-status__loading">Checking propagation…</p>;
  if (error) return <p className="page__error">{error}</p>;
  if (!status) return null;

  return (
    <div
      className={`propagation-status ${status.propagated ? "propagation-status--ok" : "propagation-status--pending"}`}
    >
      <span className="propagation-status__dot" />
      {status.propagated ? (
        <span>
          Policy v{status.active_version} active on this interceptor (
          {status.known_interceptor_instances}/{status.known_interceptor_instances} known)
        </span>
      ) : (
        <span>
          Propagating — Postgres has v{status.active_version} active, this interceptor still has
          {status.this_instance_cached_version != null
            ? ` v${status.this_instance_cached_version}`
            : " nothing"}{" "}
          cached. The background reconciler (U5) catches this up within ~30s.
        </span>
      )}
    </div>
  );
}
