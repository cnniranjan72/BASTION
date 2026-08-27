import { useRef, useState } from "react";
import { useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import type { Mesh, MeshStandardMaterial } from "three";
import { useGraphStore } from "../../store/graph";
import { NODE_STATUS_LABEL } from "../../lib/labels";
import { colorForStatus, isTransient, radiusForNode } from "./encoding";
import { useSimulationRegistry } from "./SimulationContext";

interface NodeMeshProps {
  spanId: string;
}

// A policy decision landing is the single most demo-able moment in this
// whole system -- it needs to read as an event, not a static red ball
// someone has to already be staring at to notice. BLOCK_FLASH_SECONDS is
// how long the transition-into-blocked/failed gets a decaying ring-out
// pulse + emissive spike, layered on top of (not replacing) the steady
// color-coding encoding.ts already does.
const BLOCK_FLASH_SECONDS = 1.1;

function isBlockedLike(status: string | null): boolean {
  return status === "blocked" || status === "failed";
}

/** Subscribes to only *this* node's slice of the store — a status/cost
 * change on one node re-renders one NodeMesh, not the whole graph
 * (ARCHITECTURE.md §2.6). Position is read straight off the shared
 * simulation's node object every frame, entirely outside React state. */
export function NodeMesh({ spanId }: NodeMeshProps) {
  const node = useGraphStore((s) => s.nodes.get(spanId));
  const selectedSpanId = useGraphStore((s) => s.selectedSpanId);
  const selectNode = useGraphStore((s) => s.selectNode);
  const { simNodes } = useSimulationRegistry();
  const meshRef = useRef<Mesh>(null);
  const materialRef = useRef<MeshStandardMaterial>(null);
  const pulseRef = useRef(0);
  const prevStatusRef = useRef<string | null>(null);
  const flashElapsedRef = useRef(Number.POSITIVE_INFINITY);
  const [hovered, setHovered] = useState(false);

  useFrame((_state, delta) => {
    const simNode = simNodes.get(spanId);
    const mesh = meshRef.current;
    if (!simNode || !mesh) return;
    const x = simNode.x ?? 0;
    const y = simNode.y ?? 0;
    const z = simNode.z ?? 0;
    // Defensive, not decorative: a numerically unstable tick (the
    // numDimensions bug this file's sibling ForceGraph.tsx documents was
    // one real cause, but any future force-tuning mistake could produce
    // another) leaves d3-force nodes permanently NaN — nothing in d3-force
    // itself ever recovers from that on its own. Skipping the position
    // write for one bad frame is a far better failure mode than a node
    // silently vanishing forever because its mesh.position became NaN.
    if (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z)) {
      mesh.position.set(x, y, z);
    }

    // Arm the flash the instant a node crosses into blocked/failed — not on
    // every frame it happens to already be blocked (a replay scrub landing
    // mid-trace would otherwise flash every node on screen at once).
    if (node && node.status !== prevStatusRef.current) {
      if (isBlockedLike(node.status) && !isBlockedLike(prevStatusRef.current)) {
        flashElapsedRef.current = 0;
      }
      prevStatusRef.current = node.status;
    }

    const restingIntensity = spanId === selectedSpanId || hovered ? 1.1 : 0.45;
    if (node && isTransient(node.status)) {
      pulseRef.current += delta * 4;
      const pulse = 1 + Math.sin(pulseRef.current) * 0.08;
      mesh.scale.setScalar(pulse);
      if (materialRef.current) materialRef.current.emissiveIntensity = restingIntensity;
    } else if (flashElapsedRef.current < BLOCK_FLASH_SECONDS) {
      flashElapsedRef.current += delta;
      const t = Math.min(flashElapsedRef.current / BLOCK_FLASH_SECONDS, 1);
      const decay = 1 - t;
      // A fast decaying ring-out (5 half-cycles collapsing to 0), distinct
      // from the steady in-flight pulse above — reads as "this just
      // happened" rather than "this is still working."
      mesh.scale.setScalar(1 + Math.sin(t * Math.PI * 5) * 0.35 * decay);
      if (materialRef.current) materialRef.current.emissiveIntensity = restingIntensity + decay * 2.2;
    } else {
      mesh.scale.setScalar(1);
      if (materialRef.current) materialRef.current.emissiveIntensity = restingIntensity;
    }
  });

  if (!node) return null;

  const isSelected = spanId === selectedSpanId;
  const radius = radiusForNode(node.latency_ms, node.cost);
  const color = colorForStatus(node.status);

  return (
    <mesh
      ref={meshRef}
      onClick={(event) => {
        event.stopPropagation();
        selectNode(spanId);
      }}
      onPointerOver={(event) => {
        event.stopPropagation();
        setHovered(true);
        document.body.style.cursor = "pointer";
      }}
      onPointerOut={(event) => {
        event.stopPropagation();
        setHovered(false);
        document.body.style.cursor = "auto";
      }}
    >
      <sphereGeometry args={[radius, 24, 24]} />
      <meshStandardMaterial
        ref={materialRef}
        color={color}
        emissive={color}
        emissiveIntensity={isSelected || hovered ? 1.1 : 0.45}
        roughness={0.35}
        metalness={0.1}
      />
      {isSelected && (
        <mesh>
          <sphereGeometry args={[radius * 1.35, 24, 24]} />
          <meshBasicMaterial color={color} transparent opacity={0.15} />
        </mesh>
      )}

      {/* Always-on label so a first-time viewer isn't looking at unlabeled
          colored balls — the richer tooltip only appears on hover.
          zIndexRange capped low: drei's Html defaults to a near-max
          z-index, which otherwise floats node labels above fixed page
          chrome like the legend regardless of DOM order. */}
      <Html
        position={[0, -radius - 0.22, 0]}
        center
        distanceFactor={9}
        zIndexRange={[1, 0]}
        style={{ pointerEvents: "none" }}
      >
        <div className="node-label">{node.tool_name}</div>
      </Html>

      {/* Always-on, not hover-gated: the whole point of task 2's fix is that
          the reason a call was stopped shouldn't require finding this exact
          node in a moving 3D scene and hovering it precisely. Suppressed
          while hovered so it doesn't double up with the richer tooltip
          below, which already includes the same reason line. */}
      {!hovered && node.reason && (node.status === "blocked" || node.status === "failed") && (
        <Html
          position={[0, radius + 0.25, 0]}
          center
          distanceFactor={9}
          zIndexRange={[1, 0]}
          style={{ pointerEvents: "none" }}
        >
          <div className={`node-reason-chip node-reason-chip--${node.status}`}>{node.reason}</div>
        </Html>
      )}

      {hovered && (
        <Html
          position={[0, radius + 0.25, 0]}
          center
          distanceFactor={9}
          zIndexRange={[1, 0]}
          style={{ pointerEvents: "none" }}
        >
          <div className="node-tooltip">
            <div className="node-tooltip__title">{node.tool_name}</div>
            <div className="node-tooltip__row">
              <span
                className="node-tooltip__dot"
                style={{ background: color, boxShadow: `0 0 6px ${color}` }}
              />
              {NODE_STATUS_LABEL[node.status]}
            </div>
            {node.latency_ms != null && (
              <div className="node-tooltip__row">{node.latency_ms.toFixed(0)} ms</div>
            )}
            {node.cost != null && (
              <div className="node-tooltip__row">${node.cost.toFixed(4)}</div>
            )}
            {node.reason && <div className="node-tooltip__reason">{node.reason}</div>}
          </div>
        </Html>
      )}
    </mesh>
  );
}
