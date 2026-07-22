import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end config. The tests drive the *running* app, so bring the full stack up
 * first with `./dev.sh` (db + migrations + seeded admin + backend + frontend), then
 * run `npm run test:e2e`. baseURL points at the Vite dev server.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
