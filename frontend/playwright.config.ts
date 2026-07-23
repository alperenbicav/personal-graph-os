import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'

const here = path.dirname(fileURLToPath(import.meta.url))

// ST-08.3 end-to-end acceptance: drives a real desktop Chromium against the real packaged
// server (spawned in global-setup.ts) and the real `frontend/dist` build — not mocks. Run
// `npm run build` first, then `npm run test:e2e`.
export default defineConfig({
  testDir: './e2e',
  // ST09-F03: `e2e/*.test.mjs` are plain Vitest unit specs for pure harness logic (e.g.
  // `percentile.test.mjs`), not Playwright acceptance tests; scope Playwright to only its
  // own `*.spec.ts` files so the two runners never try to collect each other's tests.
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  globalSetup: path.join(here, 'e2e', 'global-setup.ts'),
  globalTeardown: path.join(here, 'e2e', 'global-teardown.ts'),
  use: {
    trace: 'retain-on-failure',
  },
  // Both required desktop viewports (ST08-F03) run every spec file in this directory —
  // unlock/capture/export/auth acceptance and the PWA/session-security checks alike.
  projects: [
    {
      name: 'Desktop Chromium 1440x900',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'Desktop Chromium 1024x768',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1024, height: 768 } },
    },
  ],
})
