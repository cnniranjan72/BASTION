import { Suspense } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { ForceGraph } from "./ForceGraph";

export function GraphCanvas() {
  return (
    <Canvas
      camera={{ position: [0, 0, 8], fov: 55 }}
      // Dark, low-key background so emissive node colors read as the
      // clear focal point rather than competing with the chrome.
      style={{ background: "#0b0f19" }}
    >
      <ambientLight intensity={0.5} />
      <pointLight position={[5, 5, 5]} intensity={40} />
      <pointLight position={[-5, -3, -5]} intensity={15} color="#38bdf8" />
      <Suspense fallback={null}>
        <ForceGraph />
      </Suspense>
      <OrbitControls enableDamping dampingFactor={0.1} minDistance={2} maxDistance={30} />
    </Canvas>
  );
}
