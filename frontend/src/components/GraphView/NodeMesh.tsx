import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import type { Mesh } from "three";
import { useGraphStore } from "../../store/graph";
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

  useFrame((_state, delta) => {
    const simNode = simNodes.get(spanId);
    const mesh = meshRef.current;
    if (!simNode || !mesh) return;
    mesh.position.set(simNode.x ?? 0, simNode.y ?? 0, simNode.z ?? 0);

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
    >
      <sphereGeometry args={[radius, 24, 24]} />
      <meshStandardMaterial
        color={color}
        emissive={color}
        emissiveIntensity={isSelected ? 1.1 : 0.45}
        roughness={0.35}
        metalness={0.1}
      />
      {isSelected && (
        <mesh>
          <sphereGeometry args={[radius * 1.35, 24, 24]} />
          <meshBasicMaterial color={color} transparent opacity={0.15} />
        </mesh>
      )}
    </mesh>
  );
}
