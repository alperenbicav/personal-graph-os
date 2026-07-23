/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import { configDefaults } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// `index.html`'s inert `%VITE_BUILD_MARKER%` build-differentiation seam (ST09-F05) only
// avoids Vite's undefined-env-var warning and an unresolved placeholder in ordinary
// production output if the variable is defined; give it a deterministic canonical default
// so every normal build (no env override) resolves to the same literal value (ST09-F08).
if (!process.env.VITE_BUILD_MARKER) {
  process.env.VITE_BUILD_MARKER = 'default'
}

// https://vite.dev/config/
export default defineConfig({
  // The backend serves this build only under `/app/` (ST-08.1); every asset/manifest/
  // service-worker URL must be built relative to that same prefix, in dev and production.
  base: '/app/',
  plugins: [
    react(),
    VitePWA({
      // We show our own update-ready prompt and call the returned `updateSW()` ourselves
      // (main.tsx) instead of the plugin silently activating a new worker mid-session,
      // which could otherwise serve a new HTML shell against still-loaded old JS.
      registerType: 'prompt',
      injectRegister: false,
      manifest: {
        name: 'Personal Graph OS',
        short_name: 'Graph OS',
        description: 'Local-first personal graph operating system',
        start_url: '/app/',
        scope: '/app/',
        display: 'standalone',
        theme_color: '#0d0b14',
        background_color: '#0d0b14',
        icons: [
          { src: 'pwa-64x64.png', sizes: '64x64', type: 'image/png' },
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          {
            src: 'maskable-icon-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        // Precache only the built shell/assets. No `runtimeCaching` entry is configured
        // anywhere in this file: the generated worker therefore has no route at all for
        // root API, `/mcp`, `/export`, or attachment traffic, never sees the bearer token,
        // and can implement no background sync or mutation queue.
        globPatterns: ['**/*.{js,css,html,svg,png,ico,webmanifest}'],
        cleanupOutdatedCaches: true,
      },
      devOptions: {
        // Never register a service worker under `npm run dev` or Vitest; only a real
        // production build produces one.
        enabled: false,
      },
    }),
  ],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // e2e/*.spec.ts holds Playwright acceptance specs (run via `npm run test:e2e`), not
    // Vitest unit/component tests; Playwright's own `test`/`expect` API isn't
    // Vitest-compatible. `e2e/percentile.mjs`'s pure logic is a plain Vitest unit target
    // (ST09-F03), so only the Playwright spec files are excluded, not the whole directory.
    exclude: [...configDefaults.exclude, 'e2e/*.spec.ts'],
  },
})
