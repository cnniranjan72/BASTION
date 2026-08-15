import { useGraphStore } from "../../store/graph";
import { colorForStatus } from "./encoding";

/** U15 (v2 upgrade), FRONTEND_V2.md's Live Execution Graph flagship
 * requirement #3: "a horizontal event timeline below the graph; clicking
 * a timeline entry highlights the corresponding node, and clicking a node
 * scrolls/highlights the timeline. This pairing is what turns 'a graph'
 * into 'a debugger.'"
 *
 * Backed by store/graph.ts's `timeline` — an ordered, append-only log of
 * every live delta this session actually applied (see that file's
 * TimelineEntry comment for why timestamps are client-receive time, not
 * server event time, for this *live* view specifically). */
export function TimelineStrip() {
  const timeline = useGraphStore((s) => s.timeline);
  const selectedSpanId = useGraphStore((s) => s.selectedSpanId);
  const selectNode = useGraphStore((s) => s.selectNode);

  if (timeline.length === 0) {
    return (
      <div className="timeline timeline--empty">
        <p>No activity yet — the timeline fills in as this agent runs.</p>
      </div>
    );
  }

  return (
    <div className="timeline" role="list" aria-label="Event timeline">
      {timeline.map((entry) => (
        <button
          key={entry.id}
          role="listitem"
          className={`timeline__entry${entry.span_id === selectedSpanId ? " timeline__entry--selected" : ""}`}
          onClick={() => selectNode(entry.span_id)}
          title={`${entry.tool_name ?? entry.span_id} — ${entry.type}`}
        >
          <span
            className="timeline__dot"
            style={{ background: entry.status ? colorForStatus(entry.status) : "var(--text-dim)" }}
          />
          <span className="timeline__time">
            {new Date(entry.timestamp).toLocaleTimeString(undefined, {
              hour12: false,
              minute: "2-digit",
              second: "2-digit",
            })}
          </span>
          <span className="timeline__label">
            {entry.tool_name ?? entry.span_id.slice(0, 8)}
            {entry.type === "node_updated" && entry.status ? ` → ${entry.status}` : ""}
          </span>
        </button>
      ))}
    </div>
  );
}
