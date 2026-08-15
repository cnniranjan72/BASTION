import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Separate from vite.config.ts (not merged via `mergeConfig`) because the
// dev server's proxy config has no meaning under Vitest's node/jsdom
// runner — keeping them apart avoids a proxy block that looks load-bearing
// but silently does nothing in tests.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    css: false,
  },
});
