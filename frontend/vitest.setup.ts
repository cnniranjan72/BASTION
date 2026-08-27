import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import "@testing-library/jest-dom/vitest";

// jsdom doesn't implement ResizeObserver at all -- real browsers do, this is
// purely a test-environment gap. Needed once LoginPage/SignupPage started
// rendering AmbientGraphBackground's react-three-fiber <Canvas>, which uses
// react-use-measure (via ResizeObserver) to size itself; without this every
// render() touching either page throws before any assertion runs. A no-op
// stub is correct here, not a loosened test -- nothing about layout
// measurement is actually asserted on in jsdom either way.
//
// This file compiles under tsconfig.node.json (lib: ES2023, no DOM), so the
// global `ResizeObserver` type itself isn't visible here -- routed entirely
// through `unknown`, not `any`.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const globalWithResizeObserver = globalThis as unknown as { ResizeObserver?: unknown };
globalWithResizeObserver.ResizeObserver ??= ResizeObserverStub;

// No `test.globals: true` in vitest.config.ts (deliberate — keeps `describe`/
// `it`/`expect` explicit imports, consistent with this project's preference
// for explicit over ambient elsewhere), so React Testing Library's usual
// auto-cleanup-via-globals never registers on its own. Without this, every
// `render()` in a file after the first leaves its DOM tree mounted, and a
// later query like `getByRole` finds N stacked copies instead of one.
afterEach(cleanup);
