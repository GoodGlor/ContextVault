import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the FastAPI backend so the SPA and API share an
// origin in development (no CORS, cookies/headers flow through). The API client
// prefixes every request with /api (see src/api/client.ts).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: false,
  },
});
