// d3-force-3d ships no type declarations and no @types/d3-force-3d package
// exists on npm (checked: 404). This is a deliberately loose ambient
// declaration covering only what GraphCanvas.tsx actually uses — the
// simulation's per-tick node mutation (x/y/z written in place) is
// inherently untyped in d3-force's own design anyway, so a fuller typing
// wouldn't buy much safety here.
//
// forceSimulation's `numDimensions` second parameter was missing from this
// declaration entirely until this comment was added: the library's own
// default (simulation.js: `numDimensions = numDimensions || 2`) is **2**,
// not 3 — despite the package being named d3-force-3d. Omitting it here
// meant ForceGraph.tsx's `forceSimulation<SimNode>([])` could never have
// passed 3 even if it tried (TypeScript would reject the extra arg), so
// the live/replay graph's physics silently ran in 2D the whole time —
// forceManyBody built a quadtree instead of an octree, and neither it nor
// forceLink ever touched z (see their nDim-gated branches in
// node_modules/d3-force-3d/src/{manyBody,link}.js). Found while
// investigating production reports of nodes rendering invisibly with
// several concurrent live nodes present — a real, verified-from-source
// bug in the simulation's dimensionality regardless of whether it's the
// full explanation for that report.

declare module "d3-force-3d" {
  export interface SimulationNodeDatum {
    id?: string;
    x?: number;
    y?: number;
    z?: number;
    vx?: number;
    vy?: number;
    vz?: number;
    fx?: number | null;
    fy?: number | null;
    fz?: number | null;
    [key: string]: unknown;
  }

  export interface SimulationLinkDatum<N extends SimulationNodeDatum> {
    source: string | N;
    target: string | N;
  }

  export interface Simulation<N extends SimulationNodeDatum> {
    nodes(): N[];
    nodes(nodes: N[]): this;
    force(name: string): unknown;
    force(name: string, force: unknown): this;
    alpha(): number;
    alpha(alpha: number): this;
    alphaTarget(target: number): this;
    tick(iterations?: number): this;
    stop(): this;
    restart(): this;
  }

  export interface ForceLink<N extends SimulationNodeDatum, L extends SimulationLinkDatum<N>> {
    (alpha: number): void;
    links(): L[];
    links(links: L[]): this;
    id(fn: (node: N) => string): this;
    distance(fn: number | ((link: L) => number)): this;
    strength(fn: number | ((link: L) => number)): this;
  }

  export interface ForceManyBody<N extends SimulationNodeDatum> {
    (alpha: number): void;
    strength(fn: number | ((node: N) => number)): this;
    distanceMin(distance: number): this;
    distanceMax(distance: number): this;
  }

  export interface ForceCenter {
    (alpha: number): void;
    strength(strength: number): this;
  }

  export function forceSimulation<N extends SimulationNodeDatum>(
    nodes?: N[],
    numDimensions?: number,
  ): Simulation<N>;
  export function forceManyBody<N extends SimulationNodeDatum>(): ForceManyBody<N>;
  export function forceLink<N extends SimulationNodeDatum, L extends SimulationLinkDatum<N>>(
    links?: L[],
  ): ForceLink<N, L>;
  export function forceCenter(x?: number, y?: number, z?: number): ForceCenter;
}
