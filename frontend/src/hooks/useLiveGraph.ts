import { useEffect, useRef, useState } from "react";
import { liveWebSocketUrl } from "../api/client";
import { useGraphStore } from "../store/graph";
import type { LiveMessage } from "../api/types";

export type ConnectionStatus = "connecting" | "open" | "closed" | "error";

/** Subscribes to WS /live/{agentId} and applies every delta straight into
 * the graph store — this is the Phase 6 "no polling" contract; the effect
 * only re-subscribes when agentId itself changes, not on every render. */
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
    reset();
    setStatus("connecting");
    const socket = new WebSocket(liveWebSocketUrl(agentId));

    socket.onopen = () => setStatus("open");
    socket.onerror = () => setStatus("error");
    socket.onclose = () => setStatus("closed");
    socket.onmessage = (event: MessageEvent<string>) => {
      const message = JSON.parse(event.data) as LiveMessage;
      applyRef.current(message);
    };

    return () => socket.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset/applyLiveMessage are stable zustand actions
  }, [agentId]);

  return status;
}
