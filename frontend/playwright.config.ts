import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'

const here = path.dirname(fileURLToPath(import.meta.url))

// ST-08.3 end-to-end acceptance: drives a real desktop Chromium against the real packaged
// server (spawned in global-setup.ts) and the real `frontend/dist` build — not mocks. Run
// `npm run build` first, then `npm run test:e2e`.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  globalSetup: path.join(here, 'e2e', 'global-setup.ts'),
  globalTeardown: path.join(here, 'e2e', 'global-teardown.ts'),
  use: {
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'Desktop Chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
})
