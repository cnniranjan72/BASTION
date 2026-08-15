import { useGraphStore } from "../store/graph";
import { NODE_STATUS_LABEL } from "../lib/labels";
import { colorForStatus } from "./GraphView/encoding";

interface InspectorPanelProps {
  agentId?: string | null;
}

/** The "2D inspector" ARCHITECTURE.md §2.6 calls out as where actual
 * debugging happens — the 3D view is the wow, this is the substance:
 * full payload, timing, and policy reasoning for whatever node is
 * selected.
 *
 * U15 (v2 upgrade): also the one place FRONTEND_V2.md's shared "Why?"
 * affordance lives for now — the "Why?" block below is the same decision
 * context (policy match + condition + reason) any decision surface
 * should expose. Scoped explicitly: extracting this into its own
 * cross-screen component (for Approval Center, Threat Center, etc.) is
 * deferred along with those screens themselves — see docs/PROGRESS.md's
 * U15 entry. */
export function InspectorPanel({ agentId }: InspectorPanelProps) {
  const selectedSpanId = useGraphStore((s) => s.selectedSpanId);
  const node = useGraphStore((s) => (s.selectedSpanId ? s.nodes.get(s.selectedSpanId) : null));
  const selectNode = useGraphStore((s) => s.selectNode);

  if (!selectedSpanId || !node) {
    return (
      <aside className="inspector inspector--empty">
        <p>Click a node to inspect it.</p>
      </aside>
    );
  }

  const isDecided = node.status !== "pending";

  return (
    <aside className="inspector">
      <div className="inspector__header">
        <span
          className="inspector__status-dot"
          style={{ background: colorForStatus(node.status) }}
        />
        <h2>{node.tool_name}</h2>
        <button className="inspector__close" onClick={() => selectNode(null)} aria-label="Close">
          ×
        </button>
      </div>

      <dl className="inspector__fields">
        <dt>status</dt>
        <dd>{NODE_STATUS_LABEL[node.status]}</dd>

        {agentId && (
          <>
            <dt>agent</dt>
            <dd className="inspector__mono">{agentId}</dd>
          </>
        )}

        {node.trace_id && (
          <>
            <dt>trace_id</dt>
            <dd className="inspector__mono">{node.trace_id}</dd>
          </>
        )}

        <dt>span_id</dt>
        <dd className="inspector__mono">{node.span_id}</dd>

        {node.parent_span_id && (
          <>
            <dt>parent_span_id</dt>
            <dd className="inspector__mono">{node.parent_span_id}</dd>
          </>
        )}

        {node.updated_at != null && (
          <>
            <dt>last update</dt>
            <dd>{new Date(node.updated_at).toLocaleTimeString()}</dd>
          </>
        )}

        {node.latency_ms != null && (
          <>
            <dt>latency</dt>
            <dd>{node.latency_ms.toFixed(1)} ms</dd>
          </>
        )}

        {node.cost != null && (
          <>
            <dt>cost</dt>
            <dd>${node.cost.toFixed(4)}</dd>
          </>
        )}
      </dl>

      {isDecided && (
        <>
          <h3>Why?</h3>
          <p className="inspector__why">
            {node.reason ??
              (node.status === "allowed"
                ? "Allowed — no policy rule blocked this call."
                : "No reason recorded for this decision.")}
          </p>
        </>
      )}

      {node.args && Object.keys(node.args).length > 0 && (
        <>
          <h3>args</h3>
          <pre className="inspector__json">{JSON.stringify(node.args, null, 2)}</pre>
        </>
      )}
    </aside>
  );
}
