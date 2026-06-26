import { defineConfig, devices } from '@playwright/test'

/**
 * Functional E2E config (Task 34) — drives the RUNNING stdlib dashboard server
 * with the `@playwright/test` framework (the `ui-verification` skill), separate
 * from the visual pass in `scripts/capture-screenshots.mjs`.
 *
 * The server is started out-of-band (`scripts/agentware dashboard --no-open
 * --port 8799`) and reused; set `E2E_BASE_URL` to point at another origin.
 * `webServer` is intentionally omitted because the backend is a Python process
 * launched from the repo root, not an npm script — the spec asserts against the
 * already-running, real/synthetic-backed dashboard.
 */
const BASE_URL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8799'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: BASE_URL,
    trace: 'off',
    // Same-origin only — never let a spec reach the public internet.
    bypassCSP: false,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
