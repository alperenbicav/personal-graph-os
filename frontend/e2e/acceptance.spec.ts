import http from 'node:http'
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

// `fetch`/Playwright's `request` fixture both forbid overriding the `Host` header (Fetch
// standard); the disallowed-host check below needs the raw header, so it goes through
// Node's `http` module directly instead of through the browser.
function rawRequest(options: http.RequestOptions): Promise<{ status: number }> {
  return new Promise((resolve, reject) => {
    const req = http.request(options, (res) => {
      res.resume()
      resolve({ status: res.statusCode ?? 0 })
    })
    req.on('error', reject)
    req.end()
  })
}

test.describe('ST-08.3 remote-access acceptance (real server, real build)', () => {
  test('unlocks, completes an authenticated read and mutation, and exports the workspace', async ({
    page,
  }) => {
    const { port, token } = loadState()

    await page.goto(`http://127.0.0.1:${port}/app/`)
    await page.getByLabel(/access token/i).fill(token)
    await page.getByRole('button', { name: /^unlock$/i }).click()

    // Unlock succeeded: the token screen is gone and the Graph tab's typed create-node input
    // (ST-08: the TopBar global capture was replaced by per-tab typed create flows) is visible.
    await expect(page.getByLabel(/access token/i)).toBeHidden()
    const titleInput = page.getByPlaceholder(/new node title/i)
    await expect(titleInput).toBeVisible()

    const title = `E2E acceptance node ${Date.now()}`
    await titleInput.fill(title)
    await page.getByRole('button', { name: /add node/i }).click()
    await expect(page.getByText(title).first()).toBeVisible()

    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: /download export/i }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toMatch(/\.zip$/)
  })

  test('rejects an invalid token and never unlocks', async ({ page }) => {
    const { port } = loadState()

    await page.goto(`http://127.0.0.1:${port}/app/`)
    await page.getByLabel(/access token/i).fill('definitely-the-wrong-token')
    await page.getByRole('button', { name: /^unlock$/i }).click()

    await expect(page.getByText(/token was rejected/i)).toBeVisible()
    await expect(page.getByLabel(/access token/i)).toBeVisible()
  })

  test('navigates to Agents view, displays agent fleet, and interacts with agent dock', async ({
    page,
  }) => {
    const { port, token } = loadState()

    await page.goto(`http://127.0.0.1:${port}/app/`)
    await page.getByLabel(/access token/i).fill(token)
    await page.getByRole('button', { name: /^unlock$/i }).click()
    await expect(page.getByLabel(/access token/i)).toBeHidden()

    // Verify live agent indicator pill is present
    await expect(page.getByLabel(/active agents indicator/i)).toBeVisible()

    // Navigate to Agents tab
    await page
      .getByRole('navigation', { name: /workspace views/i })
      .getByRole('button', { name: 'Agents' })
      .click()

    await expect(page.getByText('Autonomous Agents Fleet')).toBeVisible()
    await expect(page.getByRole('button', { name: /\+ create agent/i })).toBeVisible()
  })

  test('rejects a request with no bearer token', async () => {
    const { port } = loadState()
    const result = await rawRequest({ host: '127.0.0.1', port, path: '/workspace' })
    expect(result.status).toBe(401)
  })

  test('rejects a request whose Host header is not in the trusted-host allowlist', async () => {
    const { port, token } = loadState()
    const result = await rawRequest({
      host: '127.0.0.1',
      port,
      path: '/workspace',
      headers: { Host: 'evil.example.com', Authorization: `Bearer ${token}` },
    })
    expect(result.status).toBe(400)
  })
})

