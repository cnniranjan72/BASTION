import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import "@testing-library/jest-dom/vitest";

// No `test.globals: true` in vitest.config.ts (deliberate — keeps `describe`/
// `it`/`expect` explicit imports, consistent with this project's preference
// for explicit over ambient elsewhere), so React Testing Library's usual
// auto-cleanup-via-globals never registers on its own. Without this, every
// `render()` in a file after the first leaves its DOM tree mounted, and a
// later query like `getByRole` finds N stacked copies instead of one.
afterEach(cleanup);
