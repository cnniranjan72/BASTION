import type { RawEvent } from "../api/types";

/** U15 (v2 upgrade), Incident Replay's timeline — turns a raw event into
 * the one-line human description FRONTEND_V2.md's own example shows
 * ("00:00.854 Agent requests payments.transfer"). */
export function describeEvent(event: RawEvent): string {
  const p = event.payload;
  switch (event.event_type) {
    case "CallAttempted":
      return `Agent requests ${(p.tool_name as string | undefined) ?? "unknown tool"}`;
    case "PolicyEvaluated":
      return "Policy evaluated";
    case "CallAllowed":
      return "ALLOWED";
    case "CallBlocked":
      return `BLOCKED — ${(p.reason as string | undefined) ?? "policy rule matched"}`;
    case "CallPendingApproval":
      return "Waiting on human approval";
    case "ApprovalGranted":
      return "Approved by human";
    case "ApprovalDenied":
      return `Denied by human — ${(p.reason as string | undefined) ?? "no reason given"}`;
    case "CallCompleted": {
      const latency = p.latency_ms as number | undefined;
      return `Completed${latency != null ? ` (${latency.toFixed(0)}ms)` : ""}`;
    }
    case "CallFailed":
      return `Failed — ${(p.error as string | undefined) ?? "unknown error"}`;
    default:
      return event.event_type;
  }
}

/** "00:03.854" style — minutes:seconds.millis relative to the first
 * event's real timestamp, matching FRONTEND_V2.md's example format
 * exactly. */
export function formatRelativeTime(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const millis = Math.floor(ms % 1000);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}
