import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Dashboard/policy/approval API — interceptor (Phase 1-5).
      "/api/interceptor": {
        target: "http://localhost:4001",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/interceptor/, ""),
      },
      // Traces + live WS — aggregator (Phase 4/6).
      "/api/aggregator": {
        target: "http://localhost:4002",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/aggregator/, ""),
      },
      "/ws/live": {
        target: "ws://localhost:4002",
        ws: true,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/ws\/live/, "/live"),
      },
    },
  },
});
