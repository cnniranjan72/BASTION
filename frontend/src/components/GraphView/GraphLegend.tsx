import { colorForStatus } from "./encoding";
import { NODE_STATUS_DESCRIPTION, NODE_STATUS_LABEL } from "../../lib/labels";
import type { NodeStatus } from "../../api/types";

const ORDER: NodeStatus[] = [
  "pending",
  "allowed",
  "pending_approval",
  "completed",
  "blocked",
  "failed",
];

// The 3D graph communicates everything through node color and size, which
// only means something once you know the code — this spells it out right
// on the canvas instead of making a first-time viewer guess.
export function GraphLegend() {
  return (
    <div className="graph-legend">
      <div className="graph-legend__title">What the colors mean</div>
      {ORDER.map((status) => (
        <div key={status} className="graph-legend__row" title={NODE_STATUS_DESCRIPTION[status]}>
          <span
            className="graph-legend__dot"
            style={{ background: colorForStatus(status), boxShadow: `0 0 6px ${colorForStatus(status)}` }}
          />
          {NODE_STATUS_LABEL[status]}
        </div>
      ))}
      <div className="graph-legend__hint">Bigger sphere = higher cost or latency. Hover a node for details.</div>
    </div>
  );
}
