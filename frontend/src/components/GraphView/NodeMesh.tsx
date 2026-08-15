import { useRef, useState } from "react";
import { useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import type { Mesh } from "three";
import { useGraphStore } from "../../store/graph";
import { NODE_STATUS_LABEL } from "../../lib/labels";
import { colorForStatus, isTransient, radiusForNode } from "./encoding";
import { useSimulationRegistry } from "./SimulationContext";

interface NodeMeshProps {
  spanId: string;
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
  const pulseRef = useRef(0);
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

    if (node && isTransient(node.status)) {
      pulseRef.current += delta * 4;
      const pulse = 1 + Math.sin(pulseRef.current) * 0.08;
      mesh.scale.setScalar(pulse);
    } else {
      mesh.scale.setScalar(1);
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
