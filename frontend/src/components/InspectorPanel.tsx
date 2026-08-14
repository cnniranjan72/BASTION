import { useGraphStore } from "../store/graph";
import { colorForStatus } from "./GraphView/encoding";

/** The "2D inspector" ARCHITECTURE.md §2.6 calls out as where actual
 * debugging happens — the 3D view is the wow, this is the substance:
 * full payload, timing, and policy reasoning for whatever node is
 * selected. */
export function InspectorPanel() {
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
        <dd>{node.status}</dd>

        <dt>span_id</dt>
        <dd className="inspector__mono">{node.span_id}</dd>

        {node.parent_span_id && (
          <>
            <dt>parent_span_id</dt>
            <dd className="inspector__mono">{node.parent_span_id}</dd>
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

        {node.reason && (
          <>
            <dt>reason</dt>
            <dd>{node.reason}</dd>
          </>
        )}
      </dl>

      {node.args && Object.keys(node.args).length > 0 && (
        <>
          <h3>args</h3>
          <pre className="inspector__json">{JSON.stringify(node.args, null, 2)}</pre>
        </>
      )}
    </aside>
  );
}
