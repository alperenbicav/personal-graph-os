import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import { STATE_PATH } from './global-setup.js'

interface AcceptanceState {
  port: number
  token: string
}

function loadState(): AcceptanceState {
  return JSON.parse(readFileSync(STATE_PATH, 'utf-8')) as AcceptanceState
}

// ST08-F03: durable regression gate for the PWA contract at both required desktop
// viewports (see playwright.config.ts's two projects) — service worker scope, cache
// contents, offline shell, and reconnect recovery, not just source-string assertions.
test.describe('ST-08.2 PWA acceptance (real server, real build)', () => {
  test('service worker registers at /app/ scope, caches only public shell assets, and recovers after offline/reconnect', async ({
    page,
    context,
  }) => {
    const { port, token } = loadState()
    await page.goto(`http://127.0.0.1:${port}/app/`)

    const registration = await page.evaluate(async () => {
      const reg = await navigator.serviceWorker.ready
      return { scope: reg.scope }
    })
    expect(new URL(registration.scope).pathname).toBe('/app/')

    const cachedUrls = await page.evaluate(async () => {
      const cacheNames = await caches.keys()
      const urls: string[] = []
      for (const name of cacheNames) {
        const cache = await caches.open(name)
        const requests = await cache.keys()
        urls.push(...requests.map((request) => request.url))
      }
      return urls
    })
    expect(cachedUrls.length).toBeGreaterThan(0)
    const forbiddenPathPrefixes = [
      '/workspace',
      '/mcp',
      '/export',
      '/attachments',
      '/nodes',
      '/edges',
    ]
    for (const url of cachedUrls) {
      const pathname = new URL(url).pathname
      expect(pathname.startsWith('/app/')).toBe(true)
      expect(forbiddenPathPrefixes.some((prefix) => pathname.startsWith(prefix))).toBe(false)
    }

    // Nothing sensitive lives in Local Storage either (the session lives in `history.state`,
    // scoped to this tab only).
    expect(await page.evaluate(() => window.localStorage.length)).toBe(0)

    await page.getByLabel(/access token/i).fill(token)
    await page.getByRole('button', { name: /^unlock$/i }).click()
    await expect(page.getByPlaceholder(/capture a task, note, or link/i)).toBeVisible()

    // Offline: the app shell must still load from the worker's cache, and the UI must show
    // an explicit backend-unavailable state rather than hang or silently show stale data.
    await context.setOffline(true)
    await page.reload()
    await expect(page.getByText(/could not reach the personal graph os api/i)).toBeVisible()

    // Reconnect: a plain reload recovers full functionality.
    await context.setOffline(false)
    await page.reload()
    await expect(page.getByPlaceholder(/capture a task, note, or link/i)).toBeVisible()
  })
})
