import { useEffect, useRef, useState } from "react";
import { liveWebSocketUrl } from "../api/client";
import { useGraphStore } from "../store/graph";
import type { LiveMessage } from "../api/types";

export type ConnectionStatus = "connecting" | "open" | "closed" | "error" | "reconnecting";

const RECONNECT_DELAYS_MS = [500, 1000, 2000, 4000, 8000];

/** Subscribes to WS /live/{agentId} and applies every delta straight into
 * the graph store — this is the Phase 6 "no polling" contract; the effect
 * only re-subscribes when agentId itself changes, not on every render.
 *
 * U14/U15 (v2 upgrade): a dropped connection reconnects with backoff
 * rather than giving up — each fresh connection triggers the server's own
 * resync burst (aggregator/main.py's _send_resync_snapshot, U14's chaos
 * finding/fix) that replays current state for every still-running trace,
 * so `reset()` before each attempt (including reconnects, not just the
 * first) is what makes "drop mid-session, reconnect, see full current
 * state" actually true end to end — the fix isn't only server-side. */
export function useLiveGraph(agentId: string | null): ConnectionStatus {
  const applyLiveMessage = useGraphStore((s) => s.applyLiveMessage);
  const reset = useGraphStore((s) => s.reset);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");

  // Keep the latest applyLiveMessage without re-running the connect effect
  // when the store's function identity changes across renders.
  const applyRef = useRef(applyLiveMessage);
  applyRef.current = applyLiveMessage;

  useEffect(() => {
    if (!agentId) {
      setStatus("closed");
      return;
    }

    let attempt = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let currentSocket: WebSocket | null = null;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      reset();
      setStatus(attempt === 0 ? "connecting" : "reconnecting");
      const socket = new WebSocket(liveWebSocketUrl(agentId));
      currentSocket = socket;

      socket.onopen = () => {
        attempt = 0;
        setStatus("open");
      };
      socket.onerror = () => setStatus("error");
      socket.onclose = (event) => {
        if (stopped) return;
        // 4401/4403 are application-level auth/authz rejections (bad or
        // missing token, cross-org agent_id) — retrying with the same
        // token would just fail identically, so don't loop forever on it.
        if (event.code === 4401 || event.code === 4403) {
          setStatus("error");
          return;
        }
        setStatus("closed");
        const delay = RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)];
        attempt += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
      socket.onmessage = (event: MessageEvent<string>) => {
        const message = JSON.parse(event.data) as LiveMessage;
        applyRef.current(message);
      };
    };

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      currentSocket?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset/applyLiveMessage are stable zustand actions
  }, [agentId]);

  return status;
}
